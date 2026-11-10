"""
title: Email Composer Auto Display
author: Joost Heijden
version: 0.2.0
description: Automatically display prepared email cards using the installed Email Composer Action.
required_open_webui_version: 0.13.3
"""
import json
import re

INSTRUCTIONS = "When the user asks to compose an email, return a single fenced `email-composer` JSON block with these string fields: `to`, `cc`, `bcc`, `subject`, `html`, `priority`. The installed Email Composer Auto Display filter automatically replaces this prepared block with the visual editor; do not tell the user to click a toolbar action. Use empty recipient fields when unknown; never invent email addresses. `priority` is `low`, `normal`, or `high`. Use only paragraphs, line breaks, bold, italic, underline, lists and HTTP(S) links in `html`. Do not include scripts, styles, images, or attachments. Write email content in the user's language. Preparing the card does not save a mailbox draft or send mail. The user reviews and edits it directly and uses the named buttons inside the card. Never claim saving or sending succeeded without an actual result."
from pydantic import BaseModel, Field


class Filter:
    class Valves(BaseModel):
        action_id: str = Field(default='email_composer', description='Installed Email Composer Action Function ID')
        priority: int = Field(default=20)

    def __init__(self):
        self.valves = self.Valves()

    async def inlet(self, body: dict, __model__=None) -> dict:
        if self.valves.action_id not in {a.get('id', '').split('.')[0] for a in (__model__ or {}).get('actions', [])}:
            return body
        messages = body.get('messages', [])
        system = next((m for m in messages if m.get('role') == 'system' and isinstance(m.get('content'), str)), None)
        if system is None:
            messages.insert(0, {'role': 'system', 'content': INSTRUCTIONS})
        elif INSTRUCTIONS not in system['content']:
            system['content'] += '\n\n' + INSTRUCTIONS
        body['messages'] = messages
        return body

    async def outlet(self, body: dict, __user__=None, __request__=None, __event_call__=None,
                     __event_emitter__=None, __model__=None) -> dict:
        if not (__user__ and __event_call__ and __event_emitter__ and __request__):
            return body
        messages = body.get('messages', [])
        if not messages or messages[-1].get('role') != 'assistant':
            return body
        message = messages[-1]
        content = message.get('content', '')
        if not isinstance(content, str):
            return body
        match = re.search(r'```email-composer\s*\n(.*?)\n```', content, re.S)
        if not match:
            return body
        try:
            candidate = json.loads(match.group(1))
            if not isinstance(candidate, dict):
                return body
        except ValueError:
            return body
        from open_webui.models.functions import Functions
        from open_webui.utils.plugin import get_function_module_from_cache
        action_id = self.valves.action_id
        function = await Functions.get_function_by_id(action_id)
        if not function or function.type != 'action' or not function.is_active:
            raise ValueError('Activate the Email Composer Action before using Auto Display')
        # Buttons delegate to the model's native Actions; require those Actions to be available.
        actions = (__model__ or {}).get('actions', [])
        if action_id not in {a.get('id', '').split('.')[0] for a in actions}:
            return body
        action, _, _ = await get_function_module_from_cache(__request__, action_id)
        values = await Functions.get_function_valves_by_id(action_id)
        action.valves = action.Valves(**(values or {}))
        message_id = message.get('id')
        if not body.get('chat_id') or not message_id or message_id != body.get('id'):
            return body
        response = await action.action(
            {**body, 'id': message_id}, __user__=__user__, __request__=__request__,
            __event_call__=__event_call__, __id__='open')
        await __event_emitter__({'type': 'embeds', 'data': {'embeds': [response.body.decode('utf-8')], 'replace': True}})
        summary = 'Email ready to review.'
        message['content'] = summary
        # Newer Open WebUI renders saved output in preference to the legacy content string.
        message['output'] = [{'id': message_id, 'type': 'message', 'role': 'assistant',
                              'status': 'completed', 'content': [{'type': 'output_text', 'text': summary}]}]
        return body
