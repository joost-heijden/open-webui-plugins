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
CARD_HTML = '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width,initial-scale=1">\n<style>\n:root{color-scheme:light dark;font:14px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;--surface:light-dark(#fff,#202020);--text:light-dark(#202020,#ededed);--muted:light-dark(#686868,#aaa);--border:light-dark(#e5e5e5,#3b3b3b);--chip:light-dark(#efefef,#343434);--hover:light-dark(#f3f3f3,#303030);--focus:light-dark(#525252,#c4c4c4);color:var(--text);background:transparent}\n:root[data-theme=light]{color-scheme:light}:root[data-theme=dark]{color-scheme:dark}\n*{box-sizing:border-box}body{margin:0;padding:1px}button,input,select{font:inherit;color:inherit}button{cursor:pointer}button,summary{-webkit-tap-highlight-color:transparent}svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;flex-shrink:0}button:focus-visible,summary:focus-visible,select:focus-visible{outline:2px solid var(--focus);outline-offset:2px}button:disabled{cursor:default}button[hidden],[hidden]{display:none!important}\n.composer{border:1px solid var(--border);border-radius:12px;background:var(--surface);overflow:visible}header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px 20px}header strong{display:flex;align-items:center;gap:10px;font-size:15px;font-weight:650}header strong svg{width:21px;height:21px}.header-tools{display:flex;gap:8px;align-items:center}.priority-badge{font-size:11px;color:var(--muted)}details{position:relative}summary{display:flex;padding:6px;border-radius:6px;cursor:pointer;list-style:none}summary::-webkit-details-marker{display:none}summary:hover{background:var(--hover)}.options{position:absolute;z-index:2;right:0;top:36px;min-width:200px;border:1px solid var(--border);border-radius:8px;background:var(--surface);padding:12px}.options label{font-size:12px;color:var(--muted)}select{display:block;width:100%;margin-top:6px;padding:6px;border:1px solid var(--border);border-radius:5px;background:var(--surface)}\n.fields{margin:0 20px}.field{display:flex;align-items:center;gap:14px;min-height:47px;border-top:1px solid var(--border);padding:8px 0}.field-name{flex:0 0 64px;color:var(--muted);font-size:13px}.recipient{display:flex;align-items:center;flex-wrap:wrap;gap:5px;min-width:0;flex:1}.chip{display:inline-flex;align-items:center;gap:5px;max-width:100%;background:var(--chip);border-radius:16px;padding:3px 6px 3px 11px;font-size:12px}.chip span{overflow-wrap:anywhere;min-width:0}.chip button{display:flex;align-items:center;justify-content:center;background:none;border:0;border-radius:50%;padding:2px;color:var(--muted)}.chip button:hover{background:var(--border)}.chip svg{width:12px;height:12px}.recipient-entry{flex:1;width:100%;min-width:70px;border:0;outline:0;padding:4px 2px;background:transparent;font-size:13px}.recipient:has(.chip) .recipient-entry{flex:1 1 70px;width:70px}.recipient-entry:focus-visible{box-shadow:inset 0 -1px var(--focus)}.reveal{display:flex;gap:10px;margin-left:auto}.reveal button{background:none;border:0;padding:4px 0;color:var(--muted);font-size:12px}.reveal button:hover{color:var(--text)}#subject{flex:1;min-width:0;background:transparent;border:0;outline:0;padding:4px 0;font-size:14px}#subject:focus-visible{box-shadow:inset 0 -1px var(--focus)}input::placeholder{color:var(--muted);opacity:.8}\nnav{display:flex;align-items:center;gap:3px;border-top:1px solid var(--border);margin:0 20px;padding:9px 0 3px}nav button{display:flex;align-items:center;justify-content:center;width:31px;height:31px;border:0;border-radius:5px;padding:6px;background:transparent}nav button:hover,nav button[aria-pressed=true]{background:var(--hover)}nav button[aria-pressed=true]{box-shadow:inset 0 0 0 1px var(--border)}.separator{height:18px;width:1px;background:var(--border);margin:0 7px}#editor{min-height:180px;padding:14px 20px 18px;overflow-wrap:anywhere;outline:none}#editor:focus-visible{box-shadow:inset 2px 0 var(--focus)}#editor:empty:before{content:attr(data-placeholder);color:var(--muted);pointer-events:none}#editor p,#editor div{margin:0 0 14px}#editor>:last-child{margin-bottom:0}#editor ul,#editor ol{margin:0 0 14px;padding-left:24px}#editor li{margin:3px 0}#editor a{text-decoration:underline;color:inherit}#editor blockquote{border-left:2px solid var(--border);margin:0 0 14px;padding-left:14px}footer{padding:0 20px 12px;color:var(--muted);font-size:11px;overflow-wrap:anywhere}#state{white-space:pre-wrap}footer:has(#state:empty){display:none}\n@media(max-width:480px){header{padding:14px}.fields,nav{margin-inline:14px}.field{gap:8px}.field-name{flex-basis:48px}.reveal{gap:8px}#editor{padding:12px 14px 16px}footer{padding-inline:14px}.chip{font-size:11px}.recipient:has(.chip) .recipient-entry{min-width:30px;flex-basis:30px}}\n</style>\n</head>\n<body>\n<article class="composer" aria-label="Email composer">\n<header><strong><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/></svg>Email</strong><div class="header-tools"><span class="priority-badge" id="priority-badge" hidden></span><details id="options"><summary aria-label="Email options" title="Email options"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></svg></summary><div class="options"><label for="priority">Priority</label><select id="priority" aria-label="Priority"><option value="normal">Normal</option><option value="high">High</option><option value="low">Low</option></select></div></details></div></header>\n<div class="fields">\n<div class="field"><label class="field-name" for="to-entry">To</label><div class="recipient" data-recipient="to"><input type="hidden" id="to"><input class="recipient-entry" id="to-entry" aria-label="To" placeholder="name@example.com" autocomplete="off" spellcheck="false"></div><div class="reveal"><button type="button" data-reveal="cc" aria-expanded="false" aria-controls="cc-row">Cc</button><button type="button" data-reveal="bcc" aria-expanded="false" aria-controls="bcc-row">Bcc</button></div></div>\n<div class="field" id="cc-row" hidden><label class="field-name" for="cc-entry">Cc</label><div class="recipient" data-recipient="cc"><input type="hidden" id="cc"><input class="recipient-entry" id="cc-entry" aria-label="CC" placeholder="name@example.com" autocomplete="off" spellcheck="false"></div></div>\n<div class="field" id="bcc-row" hidden><label class="field-name" for="bcc-entry">Bcc</label><div class="recipient" data-recipient="bcc"><input type="hidden" id="bcc"><input class="recipient-entry" id="bcc-entry" aria-label="BCC" placeholder="name@example.com" autocomplete="off" spellcheck="false"></div></div>\n<div class="field"><label class="field-name" for="subject">Subject</label><input id="subject" aria-label="Subject" placeholder="Email subject"></div>\n</div>\n<nav aria-label="Text formatting">\n<button type="button" data-command="bold" aria-label="Bold" title="Bold" aria-pressed="false"><svg viewBox="0 0 24 24" aria-hidden="true"><path stroke-width="2.4" d="M7 4h6a4 4 0 0 1 0 8H7zm0 8h7a4 4 0 0 1 0 8H7z"/></svg></button>\n<button type="button" data-command="italic" aria-label="Italic" title="Italic" aria-pressed="false"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 4h8M6 20h8M14 4l-4 16"/></svg></button>\n<button type="button" data-command="underline" aria-label="Underline" title="Underline" aria-pressed="false"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 4v7a6 6 0 0 0 12 0V4M4 21h16"/></svg></button>\n<button type="button" id="link" aria-label="Insert link" title="Insert link"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m10 13 4-4M8 16l-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0M16 8l2-2a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0" transform="translate(2 0) scale(.85 1)"/></svg></button>\n<span class="separator" aria-hidden="true"></span>\n<button type="button" data-command="insertUnorderedList" aria-label="Bulleted list" title="Bulleted list" aria-pressed="false"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5h12M9 12h12M9 19h12"/><circle cx="3" cy="5" r="1"/><circle cx="3" cy="12" r="1"/><circle cx="3" cy="19" r="1"/></svg></button>\n<button type="button" data-command="insertOrderedList" aria-label="Numbered list" title="Numbered list" aria-pressed="false"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 5h11M10 12h11M10 19h11M2 3h1v4M2 7h3M2 11c0-2 3-2 3 0 0 1-3 3-3 3h3M2 17h3l-2 2c3-1 3 3-1 2"/></svg></button>\n</nav>\n<div id="editor" contenteditable="true" role="textbox" aria-label="Message body" aria-multiline="true" data-placeholder="Write your message…"></div>\n<footer><div id="state" role="status" aria-live="polite"></div></footer>\n</article>\n<script>\nconst initial=/*INITIAL*/;\nlet card=initial.card,revision=initial.revision,savedHash=null,hasSaved=Boolean(initial.saved_at),outcome=initial.status;\nconst ids=[\'to\',\'cc\',\'bcc\',\'subject\',\'priority\'],editor=document.getElementById(\'editor\'),status=document.getElementById(\'state\');\nconst recipients=[\'to\',\'cc\',\'bcc\'];\nconst split=value=>value.split(/[;,]/).map(s=>s.trim()).filter(Boolean);\nfunction recipientValue(id){return [...split(document.getElementById(id).value),...split(document.getElementById(id+\'-entry\').value)].join(\'; \');}\nfunction fields(){return Object.fromEntries([...ids.map(id=>[id,recipients.includes(id)?recipientValue(id):document.getElementById(id).value]),[\'html\',sanitize(editor.innerHTML)]]);}\nfunction renderRecipients(id,value){const box=document.querySelector(\'[data-recipient="\'+id+\'"]\'),entry=document.getElementById(id+\'-entry\'),addresses=split(value);document.getElementById(id).value=addresses.join(\'; \');entry.value=\'\';entry.placeholder=addresses.length?\'\':\'name@example.com\';box.querySelectorAll(\'.chip\').forEach(el=>el.remove());addresses.forEach(address=>{const chip=document.createElement(\'span\');chip.className=\'chip\';const text=document.createElement(\'span\');text.textContent=address;const remove=document.createElement(\'button\');remove.type=\'button\';remove.setAttribute(\'aria-label\',\'Remove \'+address+\' from \'+id.toUpperCase());remove.innerHTML=\'<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8"/></svg>\';remove.addEventListener(\'mousedown\',e=>e.preventDefault());remove.onclick=()=>{const current=split(recipientValue(id));current.splice([...box.querySelectorAll(\'.chip\')].indexOf(chip),1);renderRecipients(id,current.join(\'; \'));dirty();entry.focus();};chip.append(text,remove);box.insertBefore(chip,entry);});}\nfunction reveal(id,focus=false){document.getElementById(id+\'-row\').hidden=false;const button=document.querySelector(\'[data-reveal="\'+id+\'"]\');button.setAttribute(\'aria-expanded\',\'true\');button.hidden=true;if(focus)document.getElementById(id+\'-entry\').focus();}\nrecipients.forEach(id=>{const entry=document.getElementById(id+\'-entry\');const commit=()=>{renderRecipients(id,recipientValue(id));dirty();};entry.addEventListener(\'blur\',commit);entry.addEventListener(\'keydown\',e=>{if(e.key===\'Enter\'||e.key===\';\'||e.key===\',\'){e.preventDefault();commit();}else if(e.key===\'Backspace\'&&!entry.value){const addresses=split(document.getElementById(id).value);entry.value=addresses.pop()||\'\';const pending=entry.value;renderRecipients(id,addresses.join(\'; \'));entry.value=pending;dirty();}});});\ndocument.querySelectorAll(\'[data-reveal]\').forEach(b=>b.onclick=()=>reveal(b.dataset.reveal,true));\nfunction priorityBadge(){const value=document.getElementById(\'priority\').value,badge=document.getElementById(\'priority-badge\');badge.hidden=value===\'normal\';badge.textContent=value===\'high\'?\'High priority\':\'Low priority\';}\ndocument.getElementById(\'priority\').addEventListener(\'change\',priorityBadge);\ndocument.addEventListener(\'click\',e=>{if(!e.target.closest(\'#options\'))document.getElementById(\'options\').open=false;});\ndocument.addEventListener(\'keydown\',e=>{if(e.key===\'Escape\')document.getElementById(\'options\').open=false;});\nfunction sanitize(html){const doc=new DOMParser().parseFromString(html,\'text/html\');const allowed=new Set([\'P\',\'DIV\',\'BR\',\'STRONG\',\'B\',\'EM\',\'I\',\'U\',\'S\',\'UL\',\'OL\',\'LI\',\'BLOCKQUOTE\',\'A\']);function walk(node){if(node.nodeType===3)return node.textContent.replaceAll(\'&\',\'&amp;\').replaceAll(\'<\',\'&lt;\').replaceAll(\'>\',\'&gt;\');if(node.nodeType!==1)return \'\';if([\'SCRIPT\',\'STYLE\',\'IFRAME\',\'OBJECT\',\'SVG\',\'MATH\'].includes(node.tagName))return \'\';const content=[...node.childNodes].map(walk).join(\'\');if(!allowed.has(node.tagName))return content;let attr=\'\';if(node.tagName===\'A\'&&/^https?:\\/\\//i.test(node.getAttribute(\'href\')||\'\'))attr=\' href="\'+node.getAttribute(\'href\').replaceAll(\'&\',\'&amp;\').replaceAll(\'"\',\'&quot;\').replaceAll(\'<\',\'&lt;\').replaceAll(\'>\',\'&gt;\').replaceAll("\'",\'&#x27;\')+\'"\';const tag=node.tagName.toLowerCase();return \'<\'+tag+attr+\'>\'+content+(tag===\'br\'?\'\':\'</\'+tag+\'>\');}return [...doc.body.childNodes].map(walk).join(\'\');}\nfunction baseStatus(){return [\'busy\',\'unknown\'].includes(outcome)?\'Outcome uncertain. Have this card checked before saving again.\':outcome===\'sent\'?\'Send request accepted; delivery not confirmed.\':hasSaved?\'Saved to Outlook\':\'\';}\nfunction restore(value){hasSaved=Boolean(value.saved_at);outcome=value.status;recipients.forEach(id=>{renderRecipients(id,value.fields[id]);document.getElementById(id+\'-row\')?.setAttribute(\'hidden\',\'\');if(id!==\'to\'){const button=document.querySelector(\'[data-reveal="\'+id+\'"]\');button.hidden=false;button.setAttribute(\'aria-expanded\',\'false\');if(value.fields[id])reveal(id);}});[\'subject\',\'priority\'].forEach(id=>document.getElementById(id).value=value.fields[id]);editor.innerHTML=sanitize(value.fields.html);revision=value.revision;savedHash=JSON.stringify(fields());priorityBadge();status.textContent=baseStatus();}\nrestore(initial);\nfunction dirty(){const changed=JSON.stringify(fields())!==savedHash;status.textContent=[\'busy\',\'unknown\',\'sent\'].includes(outcome)?baseStatus()+(changed?\' Unsaved changes.\':\'\'):changed?\'Unsaved changes\':baseStatus();}\ndocument.addEventListener(\'input\',dirty);document.addEventListener(\'change\',dirty);\neditor.addEventListener(\'paste\',e=>{e.preventDefault();document.execCommand(\'insertText\',false,e.clipboardData.getData(\'text/plain\'));});\neditor.addEventListener(\'drop\',e=>e.preventDefault());\nfunction toolbarState(){document.querySelectorAll(\'[data-command]\').forEach(b=>b.setAttribute(\'aria-pressed\',String(document.queryCommandState(b.dataset.command))));}\ndocument.addEventListener(\'selectionchange\',()=>{const selection=getSelection();if(selection?.anchorNode&&editor.contains(selection.anchorNode))toolbarState();});\ndocument.querySelectorAll(\'nav button\').forEach(b=>b.addEventListener(\'mousedown\',e=>e.preventDefault()));\ndocument.querySelectorAll(\'[data-command]\').forEach(b=>b.onclick=()=>{if(!editor.contains(getSelection()?.anchorNode))editor.focus();document.execCommand(b.dataset.command,false,null);toolbarState();dirty();});\n// A native prompt is unavailable in sandboxed frames without allow-modals. Use an inline link form.\nconst linkForm=document.createElement(\'div\');linkForm.hidden=true;linkForm.style.cssText=\'display:flex;gap:8px;padding:8px 20px;flex-wrap:wrap\';linkForm.innerHTML=\'<input aria-label="Link URL" type="url" placeholder="https://example.com" required style="flex:1;min-width:100px;padding:6px;border:1px solid var(--border);border-radius:5px;background:transparent"><button id="insert-link" type="button" style="background:var(--chip);border:0;border-radius:5px;padding:5px 10px">Insert</button><button type="button" style="background:transparent;border:0;padding:5px">Cancel</button>\';document.querySelector(\'nav\').after(linkForm);\nlet linkRange=null;document.getElementById(\'link\').onclick=()=>{const s=getSelection();linkRange=s?.rangeCount&&editor.contains(s.anchorNode)?s.getRangeAt(0).cloneRange():null;linkForm.hidden=false;linkForm.querySelector(\'input\').focus();};\nlinkForm.querySelector(\'#insert-link\').onclick=()=>{const url=linkForm.querySelector(\'input\').value;if(!/^https?:\\/\\//i.test(url)){linkForm.querySelector(\'input\').setCustomValidity(\'Use an http:// or https:// link\');linkForm.reportValidity();return;}editor.focus();if(linkRange){const s=getSelection();s.removeAllRanges();s.addRange(linkRange);}document.execCommand(\'createLink\',false,url);linkForm.hidden=true;dirty();};linkForm.querySelector(\'input\').oninput=e=>e.target.setCustomValidity(\'\');linkForm.querySelector(\'input\').onkeydown=e=>{if(e.key===\'Enter\'){e.preventDefault();linkForm.querySelector(\'#insert-link\').click();}if(e.key===\'Escape\')linkForm.hidden=true;};linkForm.querySelector(\'button:last-child\').onclick=()=>{linkForm.hidden=true;editor.focus();};\n// Only the parent may supply a theme; all state transfer keeps the existing nonce protocol.\nwindow.addEventListener(\'message\',e=>{if(e.source===parent&&e.data?.type===\'email-composer:theme\'&&[\'light\',\'dark\'].includes(e.data.theme))document.documentElement.dataset.theme=e.data.theme;});\nparent.postMessage({type:\'email-composer:theme-request\',card},\'*\');\nwindow.addEventListener(\'message\',e=>{if(e.source!==parent||e.data?.type!==\'email-composer:request\')return;const {nonce,operation,payload}=e.data;let result={ok:true};if(operation===\'snapshot\')result={card,revision,fields:fields()};else if(payload?.card!==card)result={error:\'Card mismatch\'};else if(operation===\'restore\')restore(payload);else if(operation===\'ack\'){hasSaved=Boolean(payload.saved_at);outcome=payload.status;revision=payload.revision;savedHash=JSON.stringify(payload.fields);status.textContent=payload.status===\'sent\'?\'Send request accepted; delivery not confirmed.\':JSON.stringify(fields())===savedHash?\'Saved to Outlook\':\'Saved; newer changes are still unsaved\';}else result={error:\'Unknown operation\'};parent.postMessage({type:\'email-composer:reply\',nonce,payload:result},\'*\');});\n// Measure the card rather than a viewport-height root, so collapsed fields can shrink too.\nnew ResizeObserver(()=>parent.postMessage({type:\'iframe:height\',height:Math.ceil(document.body.getBoundingClientRect().height)},\'*\')).observe(document.body);\n</script>\n</body>\n</html>\n'


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
