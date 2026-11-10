"""Offline tests: standard-library harness; fake Open WebUI and MCP, never Graph."""
import ast
import asyncio
import copy
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
source = ast.parse((ROOT / 'email-composer/action.py').read_text())
source.body = [n for n in source.body if not (isinstance(n, ast.ImportFrom) and n.module in ('pydantic', 'fastapi.responses'))]
class BaseModel: pass
def Field(default=None, **kwargs): return default
ns = {'BaseModel': BaseModel, 'Field': Field, 'HTMLResponse': lambda *args, **kwargs: (args, kwargs)}
exec(compile(source, 'action.py', 'exec'), ns)

class Helpers(unittest.TestCase):
    def test_html_safety(self):
        self.assertEqual(ns['clean_html']('<p onclick="x">Hello<script>alert(1)</script></p>'), '<p>Hello</p>')
        self.assertEqual(ns['clean_html']('<a href="javascript:bad">x</a>'), '<a>x</a>')
    def test_exact_fields_and_clear_cc(self):
        data = ns['snapshot']({'to': 'a@example.com', 'cc': '', 'html': '<p>He said "yes" &amp; agreed.</p>'})
        body = ns['graph_body'](data)
        self.assertEqual(body['ccRecipients'], [])
        self.assertEqual(body['body']['content'], data['html'])
    def test_bad_recipients_and_markup(self):
        for raw in ({'to': 'Alice <a@example.com>'}, {'to': 'a@example.com', 'html': '<img src="https://bad">'}):
            with self.assertRaises(ValueError): ns['snapshot'](raw)
    def test_owner_revision_and_double_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ns['Store'](Path(tmp)/'cards.db')
            card = store.create('a','c','m',{'status':'local','revision':0})
            with self.assertRaises(ValueError): store.get(card,'b','c','m')
            with self.assertRaises(ValueError): store.claim(card,'a','c','m',1)
            store.claim(card,'a','c','m',0)
            with self.assertRaises(ValueError): store.claim(card,'a','c','m',0)

class Workflow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=ns['Store'](Path(self.tmp.name)/'cards.db')
        self.fields=ns['snapshot']({'to':'a@example.com','subject':'Changed by hand','html':'<p>Edited</p>'})
        self.state={'fields': self.fields,'revision':0,'status':'local','draft_id':None,'mailbox':None,'change_key':None,'saved_hash':None,'saved_at':None}
        self.card=self.store.create('u','c','m',self.state)
        self.action=ns['Action'](); self.action.store=lambda:self.store
        self.action.valves=types.SimpleNamespace(mcp_server_id='m365',enable_sending=True)
        self.action.browser=AsyncMock(return_value={'card':self.card,'revision':0,'fields':copy.deepcopy(self.fields)})
        self.event=AsyncMock(return_value=True)
        self.client=types.SimpleNamespace(call_tool=AsyncMock(side_effect=self.reply),disconnect=AsyncMock())
        names=['get-current-user','get-mail-message','create-draft-email','update-mail-message','send-draft-message']
        specs=[{'name':n,'parameters':{'properties':{'confirm':{}}}} for n in names]
        self.connector=AsyncMock(return_value=(self.client,specs))
        users=types.ModuleType('open_webui.models.users');users.Users=types.SimpleNamespace(get_user_by_id=AsyncMock(return_value=object()))
        tools=types.ModuleType('open_webui.utils.tools');tools.connect_mcp_server=self.connector
        config=types.ModuleType('open_webui.config');config.Config=types.SimpleNamespace(get=AsyncMock(return_value=[{'type':'mcp','info':{'id':'m365'},'auth_type':'oauth_2.1'}]))
        self.config=config
        self.modules=patch.dict('sys.modules',{'open_webui.config':config,'open_webui.models.users':users,'open_webui.utils.tools':tools});self.modules.start()
    async def asyncTearDown(self):self.modules.stop();self.tmp.cleanup()
    async def reply(self,name,args):
        value={'id':'mailbox'} if name=='get-current-user' else {'id':'draft','changeKey':'v1','isDraft':True}
        return [{'type':'text','text':ns['json'].dumps(value)}]
    async def run_action(self,op):
        return await self.action.action({'chat_id':'c','id':'m'},__user__={'id':'u'},__event_call__=self.event,__id__=op)
    async def test_create_then_update_same_draft(self):
        await self.run_action('save')
        saved=self.store.get(self.card,'u','c','m')
        self.assertEqual(saved['draft_id'],'draft')
        call=self.client.call_tool.call_args_list[-1]
        self.assertEqual(call.args[1]['body']['subject'],'Changed by hand')
        self.action.browser.return_value['revision']=1
        await self.run_action('save')
        self.assertEqual(self.client.call_tool.call_args_list[-1].args[0],'update-mail-message')
        self.assertEqual(self.client.call_tool.call_args_list[-1].args[1]['messageId'],'draft')
    async def test_decline_and_error_dict_are_not_approval(self):
        for value in (False, {'error':'disconnected'}):
            self.event.return_value=value
            await self.run_action('save')
        self.connector.assert_not_awaited()
    async def test_uncertain_create_blocks_retry(self):
        async def fail(name,args):
            if name=='create-draft-email': raise TimeoutError('unknown')
            return await self.reply(name,args)
        self.client.call_tool.side_effect=fail
        with self.assertRaises(TimeoutError):await self.run_action('save')
        self.assertEqual(self.store.get(self.card,'u','c','m')['status'],'unknown')
        with self.assertRaises(ValueError):await self.run_action('save')
    async def test_send_requires_saved_current_fields(self):
        with self.assertRaises(ValueError):await self.run_action('send')
        await self.run_action('save');self.action.browser.return_value['revision']=1
        self.action.browser.return_value['fields']['subject']='unsaved'
        with self.assertRaises(ValueError):await self.run_action('send')
    async def test_send_and_duplicate(self):
        await self.run_action('save');self.action.browser.return_value['revision']=1
        await self.run_action('send')
        self.assertEqual(self.store.get(self.card,'u','c','m')['status'],'sent')
        with self.assertRaises(ValueError):await self.run_action('send')
    async def test_authorization_override_rejected(self):
        self.config.Config.get.return_value[0]['headers']={'Authorization':'Bearer shared'}
        with self.assertRaises(ValueError):await self.run_action('save')
        self.connector.assert_not_awaited()
    async def test_shared_credentials_rejected(self):
        self.config.Config.get.return_value=[{'type':'mcp','info':{'id':'m365'},'auth_type':'bearer'}]
        with self.assertRaises(ValueError):await self.run_action('save')
        self.connector.assert_not_awaited()
    async def test_mailbox_switch_stops_before_mutation(self):
        self.state['mailbox']='other';self.store.put(self.card,self.state)
        with self.assertRaises(ValueError):await self.run_action('save')
        self.assertEqual(self.client.call_tool.await_count,1)

if __name__=='__main__':unittest.main()

class AutoDisplay(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tree = ast.parse((ROOT / 'email-composer/auto-display.py').read_text())
        tree.body = [n for n in tree.body if not (isinstance(n, ast.ImportFrom) and n.module == 'pydantic')]
        scope = {'BaseModel': BaseModel, 'Field': Field}
        exec(compile(tree, 'auto-display.py', 'exec'), scope)
        self.filter = scope['Filter']()
        self.filter.valves = types.SimpleNamespace(action_id='email_composer')
        self.event = AsyncMock()
        self.action = types.SimpleNamespace(Valves=lambda **kw: kw, action=AsyncMock(return_value=types.SimpleNamespace(body=b'<html>card</html>')))
        functions = types.ModuleType('open_webui.models.functions')
        functions.Functions = types.SimpleNamespace(get_function_by_id=AsyncMock(return_value=types.SimpleNamespace(type='action', is_active=True)), get_function_valves_by_id=AsyncMock(return_value={}))
        plugin = types.ModuleType('open_webui.utils.plugin')
        plugin.get_function_module_from_cache = AsyncMock(return_value=(self.action, None, None))
        self.patch = patch.dict('sys.modules', {'open_webui.models.functions': functions, 'open_webui.utils.plugin': plugin})
        self.patch.start()
        self.body = {'id':'m','chat_id':'c','messages':[{'id':'m','role':'assistant','content':'```email-composer\n'+ns['json'].dumps({'to':'','subject':'Unknown recipient','html':'<p>Hello</p>'})+'\n```'}]}
    async def asyncTearDown(self): self.patch.stop()
    async def render(self):
        return await self.filter.outlet(self.body, __user__={'id':'u'}, __request__=object(), __event_call__=AsyncMock(), __event_emitter__=self.event, __model__={'actions':[{'id':'email_composer.open'}]})
    async def test_auto_embed_replaces_json_and_output(self):
        result = await self.render()
        self.assertEqual(result['messages'][-1]['content'], 'Email ready to review.')
        self.assertEqual(result['messages'][-1]['output'][0]['content'][0]['text'], 'Email ready to review.')
        self.event.assert_awaited_once_with({'type':'embeds','data':{'embeds':['<html>card</html>'],'replace':True}})
        self.assertEqual(self.action.action.call_args.kwargs['__id__'], 'open')
    async def test_other_messages_untouched(self):
        self.body['messages'][-1]['content'] = 'An ordinary answer'
        await self.render()
        self.action.action.assert_not_awaited()
    async def test_wrong_message_binding_is_ignored(self):
        self.body['id'] = 'another'
        await self.render()
        self.action.action.assert_not_awaited()
    async def test_action_must_be_assigned(self):
        await self.filter.outlet(self.body, __user__={'id':'u'}, __request__=object(), __event_call__=AsyncMock(), __event_emitter__=self.event, __model__={'actions':[]})
        self.action.action.assert_not_awaited()
    async def test_inlet_preserves_system_and_deduplicates(self):
        value = {'messages':[{'role':'system','content':'Keep existing rules'}]}
        await self.filter.inlet(value, __model__={'actions':[{'id':'email_composer.open'}]})
        first = copy.deepcopy(value)
        await self.filter.inlet(value, __model__={'actions':[{'id':'email_composer.open'}]})
        self.assertEqual(first, value)
        self.assertTrue(value['messages'][0]['content'].startswith('Keep existing rules'))
    def test_blank_recipient_is_editable_but_cannot_be_saved(self):
        fields = {'to':'','subject':'Unknown recipient','html':'<p>Hello</p>'}
        self.assertEqual(ns['snapshot'](fields, require_recipient=False)['subject'], 'Unknown recipient')
        with self.assertRaises(ValueError): ns['snapshot'](fields)
