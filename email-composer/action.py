"""
title: Email Composer
author: Joost Heijden
version: 0.1.2
description: Editable inline email cards with user-scoped Microsoft 365 MCP draft actions. Experimental; requires deployment validation.
required_open_webui_version: 0.12.5
"""

import asyncio
import hashlib
import json
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse

# Original implementation. No code copied from the reference composer.
ALLOWED = {'p', 'div', 'br', 'strong', 'b', 'em', 'i', 'u', 's', 'ul', 'ol', 'li', 'blockquote', 'a'}


class CleanHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.blocked = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'iframe', 'object', 'svg', 'math'):
            self.blocked += 1
        if self.blocked or tag not in ALLOWED:
            return
        link = ''
        if tag == 'a':
            href = dict(attrs).get('href', '')
            if re.match(r'^https?://', href, re.I):
                link = ' href="' + escape(href, quote=True) + '"'
        self.parts.append('<' + tag + link + '>')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'iframe', 'object', 'svg', 'math'):
            self.blocked = max(0, self.blocked - 1)
            return
        if not self.blocked and tag in ALLOWED and tag != 'br':
            self.parts.append('</' + tag + '>')

    def handle_data(self, data):
        if not self.blocked:
            self.parts.append(escape(data, quote=False))


def clean_html(value):
    parser = CleanHTML()
    parser.feed(value)
    parser.close()
    return ''.join(parser.parts)


def snapshot(raw):
    if not isinstance(raw, dict):
        raise ValueError('Invalid editor response')
    result = {}
    for key in ('to', 'cc', 'bcc'):
        value = raw.get(key, '')
        if not isinstance(value, str) or len(value) > 16000:
            raise ValueError('Invalid recipient field')
        addresses = [part.strip() for part in re.split('[;,]', value) if part.strip()]
        if any(not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', address) for address in addresses):
            raise ValueError('Use plain email addresses separated by semicolons')
        if len(addresses) > 100:
            raise ValueError('Too many recipients')
        result[key] = '; '.join(addresses)
    if not result['to']:
        raise ValueError('At least one To recipient is required')
    subject, html = raw.get('subject', ''), raw.get('html', '')
    if not isinstance(subject, str) or len(subject) > 998 or '\r' in subject or '\n' in subject:
        raise ValueError('Invalid subject')
    if not isinstance(html, str) or len(html) > 200000:
        raise ValueError('Message exceeds the editor limit')
    result.update(subject=subject, html=clean_html(html), priority=raw.get('priority', 'normal'))
    if result['priority'] not in ('low', 'normal', 'high'):
        raise ValueError('Invalid priority')
    # Fail closed if pasted/modified markup would change on the server.
    if result['html'] != html:
        raise ValueError('Unsupported formatting. Use the formatting controls or paste plain text.')
    return result


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def graph_body(data):
    def recipients(key):
        return [{'emailAddress': {'address': item.strip()}} for item in data[key].split(';') if item.strip()]
    return {'subject': data['subject'], 'body': {'contentType': 'HTML', 'content': data['html']},
            'toRecipients': recipients('to'), 'ccRecipients': recipients('cc'),
            'bccRecipients': recipients('bcc'), 'importance': data['priority']}


def decode_result(blocks):
    for block in blocks:
        if block.get('type') == 'text':
            try:
                value = json.loads(block['text'])
            except (ValueError, KeyError):
                continue
            if isinstance(value, dict):
                if value.get('error'):
                    raise RuntimeError('Microsoft 365 returned an error')
                return value
    raise RuntimeError('Expected a JSON object from the MCP server; use JSON output mode')


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS cards (id TEXT PRIMARY KEY, owner TEXT, chat TEXT, message TEXT, state TEXT)')
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS card_binding ON cards(owner,chat,message)')
        Path(path).chmod(0o600)

    def db(self):
        return sqlite3.connect(self.path, timeout=10)

    def create(self, owner, chat, message, state):
        card = secrets.token_hex(16)
        with self.db() as db:
            db.execute('INSERT INTO cards VALUES (?,?,?,?,?)', (card, owner, chat, message, json.dumps(state)))
        return card

    def get(self, card, owner, chat, message):
        with self.db() as db:
            row = db.execute('SELECT state FROM cards WHERE id=? AND owner=? AND chat=? AND message=?',
                             (card, owner, chat, message)).fetchone()
        if not row:
            raise ValueError('Card not found for this user and message')
        return json.loads(row[0])

    def claim(self, card, owner, chat, message, revision):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            state = self.get(card, owner, chat, message)
            if state['status'] in ('busy', 'unknown', 'sent') or state['revision'] != revision:
                raise ValueError('Stale, busy, uncertain or already submitted card. Reload the saved card.')
            state['status'] = 'busy'
            db.execute('UPDATE cards SET state=? WHERE id=?', (json.dumps(state), card))
        return state

    def put(self, card, state):
        with self.db() as db:
            db.execute('UPDATE cards SET state=? WHERE id=?', (json.dumps(state), card))


# Standalone import: this constant is embedded by scripts/build-email-composer.py.
CARD_HTML = '__CARD_HTML__'


class Action:
    class Valves(BaseModel):
        mcp_server_id: str = Field(default='', description='ID of an existing per-user OAuth Microsoft 365 MCP connection')
        enable_sending: bool = Field(default=False, description='Enable only after deployment acceptance tests and explicit policy approval')

    def __init__(self):
        self.valves = self.Valves()
        self.actions = [{'id': key, 'name': label} for key, label in (
            ('open', 'Edit email'), ('save', 'Save draft'), ('send', 'Send email'), ('reload', 'Load saved card'))]

    def store(self):
        from open_webui.env import DATA_DIR
        return Store(Path(DATA_DIR) / 'email-composer' / 'cards.sqlite3')

    async def browser(self, event, message, operation, payload=None):
        # Fixed trusted code, never generated by a model. No cookie/storage/token reads.
        code = r'''
try {
 const box = document.getElementById(MESSAGE + '-embeds-container');
 const frames = box ? [...box.querySelectorAll('iframe')] : [];
 if (frames.length !== 1) return {error:'Expected exactly one email card on this message'};
 const frame = frames[0], nonce = NONCE;
 return await new Promise(resolve => {
  let timer;
  const done = value => { clearTimeout(timer); window.removeEventListener('message', receive); resolve(value); };
  const receive = e => { if(e.source === frame.contentWindow && e.data?.type === 'email-composer:reply' && e.data.nonce === nonce) done(e.data.payload); };
  window.addEventListener('message', receive);
  timer = setTimeout(() => done({error:'Editor did not respond'}), 5000);
  frame.contentWindow.postMessage({type:'email-composer:request', nonce, operation:OPERATION, payload:PAYLOAD}, '*');
 });
} catch(e) { return {error:'Unable to communicate with the editor'}; }
'''
        code = 'const [MESSAGE, NONCE, OPERATION, PAYLOAD] = ' + json.dumps(
            [message, secrets.token_hex(24), operation, payload], ensure_ascii=True) + ';\n' + code
        result = await asyncio.wait_for(event({'type': 'execute', 'data': {'code': code}}), timeout=10)
        if not isinstance(result, dict) or result.get('error'):
            raise ValueError('Editor communication failed. Keep this chat open and retry.')
        return result

    async def theme_bridge(self, event, message, card):
        # Open WebUI does not forward app theme changes into sandboxed HTML embeds.
        # Read only the public root theme class; never read storage or credentials.
        code = 'const [MESSAGE, CARD] = ' + json.dumps([message, card]) + ';\n' + r"""
const bridges = window.__emailComposerThemes ??= new Map();
bridges.get(MESSAGE)?.dispose();
const root = document.documentElement;
const media = matchMedia('(prefers-color-scheme: dark)');
const theme = () => root.classList.contains('dark') ? 'dark' : root.classList.contains('light') ? 'light' : media.matches ? 'dark' : 'light';
const frame = () => {
 const box = document.getElementById(MESSAGE + '-embeds-container');
 const frames = box ? box.querySelectorAll('iframe') : [];
 return frames.length === 1 ? frames[0] : null;
};
const sync = () => frame()?.contentWindow?.postMessage({type:'email-composer:theme', theme:theme()}, '*');
const receive = e => {
 if(e.source === frame()?.contentWindow && e.data?.type === 'email-composer:theme-request' && e.data.card === CARD) sync();
};
const observer = new MutationObserver(sync);
observer.observe(root, {attributes:true, attributeFilter:['class', 'style']});
window.addEventListener('message', receive);
media.addEventListener('change', sync);
bridges.set(MESSAGE, {dispose:() => {
 observer.disconnect(); window.removeEventListener('message', receive); media.removeEventListener('change', sync);
}});
sync();
return {ok:true};
"""
        await asyncio.wait_for(event({'type': 'execute', 'data': {'code': code}}), timeout=10)

    async def action(self, body, __user__=None, __request__=None, __event_call__=None, __event_emitter__=None, __id__=None):
        if not __user__ or not __event_call__:
            raise ValueError('An authenticated, connected browser is required')
        owner, chat, message = __user__['id'], body['chat_id'], body['id']
        store = self.store()
        if __id__ == 'open':
            original = next((m.get('content', '') for m in body.get('messages', []) if m.get('id') == message), '')
            if not isinstance(original, str):
                original = ''
            fields = {'to': '', 'cc': '', 'bcc': '', 'subject': '', 'priority': 'normal',
                      'html': '<p>' + escape(original[:100000]).replace('\n', '<br>') + '</p>'}
            structured = re.search(r'```email-composer\s*\n(.*?)\n```', original, re.S)
            if structured:
                try:
                    candidate = json.loads(structured.group(1))
                    candidate['html'] = clean_html(candidate.get('html', ''))
                    fields = snapshot(candidate)
                except (ValueError, TypeError, AttributeError):
                    pass  # Keep the original text when structured output is invalid.
            state = {'fields': fields, 'revision': 0, 'status': 'local', 'draft_id': None,
                     'mailbox': None, 'change_key': None, 'saved_hash': None, 'saved_at': None}
            with store.db() as db:
                existing = db.execute('SELECT id,state FROM cards WHERE owner=? AND chat=? AND message=?', (owner, chat, message)).fetchone()
            if existing:
                card, saved = existing
                state = json.loads(saved)
            else:
                card = store.create(owner, chat, message, state)
            await self.theme_bridge(__event_call__, message, card)
            initial = json.dumps({'card': card, **state}, ensure_ascii=True).replace('<', '\\u003c')
            return HTMLResponse(CARD_HTML.replace('/*INITIAL*/', initial), headers={'Content-Disposition': 'inline'})
        raw = await self.browser(__event_call__, message, 'snapshot')
        card = raw.get('card')
        state = store.get(card, owner, chat, message)
        if __id__ == 'reload':
            if state['status'] in ('busy', 'unknown'):
                raise ValueError('Outcome uncertain. Ask an administrator to reconcile this card; do not create another copy.')
            if await __event_call__({'type': 'confirmation', 'data': {'title': 'Load saved version', 'message': 'Unsaved changes in this card will be replaced. Continue?'}}) is True:
                await self.browser(__event_call__, message, 'restore', {'card': card, **state})
            return
        if __id__ not in ('save', 'send'):
            raise ValueError('Unknown action')
        fields = snapshot(raw.get('fields'))
        if __id__ == 'send':
            if not self.valves.enable_sending:
                raise ValueError('Sending is disabled by the administrator')
            if not state['draft_id'] or digest(fields) != state['saved_hash']:
                raise ValueError('Save the current fields as a draft before sending')
        # Confirm the frozen payload, not an LLM summary. HTML source is included for exact review.
        approved = await __event_call__({'type': 'confirmation', 'data': {
            'title': 'Send email' if __id__ == 'send' else 'Save Outlook draft',
            'message': '\n'.join([f"{k}: {fields[k]}" for k in ('to', 'cc', 'bcc', 'subject', 'priority')]) + '\n\n' + fields['html']}})
        if approved is not True:
            return
        state = store.claim(card, owner, chat, message, raw.get('revision'))
        client, mutation_started = None, False
        try:
            from open_webui.config import Config
            connections = await Config.get('tool_server.connections', [])
            configured = next((c for c in connections if c.get('type') == 'mcp' and (c.get('info') or {}).get('id') == self.valves.mcp_server_id), None)
            if configured and any(key.lower() == 'authorization' for key in (configured.get('headers') or {})):
                raise ValueError('Custom Authorization headers would override per-user OAuth; remove that override')
            if not configured or configured.get('auth_type') not in ('oauth_2.1', 'oauth_2.1_static'):
                raise ValueError('A per-user OAuth 2.1 MCP connection is required; shared credentials are not supported')
            from open_webui.models.users import Users
            from open_webui.utils.tools import connect_mcp_server
            user = await Users.get_user_by_id(owner)
            connection = await connect_mcp_server(__request__, self.valves.mcp_server_id, user, {})
            if not connection:
                raise ValueError('Microsoft 365 connection unavailable or access denied')
            client, specs = connection
            specs = {spec['name']: spec for spec in specs}
            async def call(name, args):
                if name not in specs:
                    raise ValueError('Required MCP tool is not enabled: ' + name)
                props = specs[name]['parameters'].get('properties', {})
                if 'confirm' in props and name in ('update-mail-message', 'send-draft-message'):
                    args = {**args, 'confirm': True}
                return decode_result(await client.call_tool(name, args))
            identity = await call('get-current-user', {})
            mailbox = identity.get('id')
            if not mailbox or (state['mailbox'] and state['mailbox'] != mailbox):
                raise ValueError('Mailbox identity changed; reconnect the original account')
            state['mailbox'] = mailbox
            if state['draft_id']:
                remote = await call('get-mail-message', {'messageId': state['draft_id']})
                if remote.get('isDraft') is not True or remote.get('changeKey') != state['change_key']:
                    raise ValueError('The Outlook draft changed externally. Operation stopped to avoid overwriting it.')
            name = 'send-draft-message' if __id__ == 'send' else ('update-mail-message' if state['draft_id'] else 'create-draft-email')
            if name not in specs:
                raise ValueError('Required MCP tool is not enabled: ' + name)
            args = {'messageId': state['draft_id']} if state['draft_id'] else {}
            if __id__ == 'save':
                args['body'] = graph_body(fields)
            state['pending'] = {'tool': name, 'fields': fields}
            store.put(card, state)
            mutation_started = True
            if __id__ == 'send':
                # Send commonly returns an empty content list, not a JSON message.
                props = specs[name]['parameters'].get('properties', {})
                if 'confirm' in props:
                    args['confirm'] = True
                await client.call_tool(name, args)
                state['status'] = 'sent'  # Submission accepted, not proof of delivery.
            else:
                result = await call(name, args)
                if not result.get('id') or not result.get('changeKey') or result.get('isDraft') is not True:
                    raise RuntimeError('Draft result lacks id, changeKey or isDraft')
                state.update(draft_id=result['id'], change_key=result['changeKey'], fields=fields,
                             saved_hash=digest(fields), saved_at=datetime.now(timezone.utc).isoformat(), status='saved')
            state.pop('pending', None)
            state['revision'] += 1
            store.put(card, state)
        except BaseException:
            state['status'] = 'unknown' if mutation_started else ('saved' if state['draft_id'] else 'local')
            store.put(card, state)
            raise
        finally:
            if client:
                await client.disconnect()
        # Keep edits made while awaiting the server; only acknowledge the submitted snapshot.
        try:
            await self.browser(__event_call__, message, 'ack', {'card': card, **state})
        except Exception:
            raise ValueError('Server operation completed. Reload the saved card before another action.')
        return
