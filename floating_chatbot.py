import streamlit.components.v1 as components


def render_floating_chatbot():
    """Render the threat-intelligence chatbot as a fixed bottom-right launcher."""
    components.html(r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box} html,body{margin:0;padding:0;width:100%;height:100%;overflow:visible;font-family:Arial,sans-serif}
#launcher{position:absolute;right:3px;bottom:3px;width:62px;height:62px;border:0;border-radius:50%;background:#155eef;color:#fff;font-size:26px;cursor:pointer;box-shadow:0 5px 22px #0005;pointer-events:auto}
#panel{display:none;position:absolute;right:0;bottom:76px;width:min(360px,calc(100vw - 12px));height:min(500px,calc(100vh - 95px));background:#0b1b30;border:1px solid #31577e;border-radius:16px;box-shadow:0 12px 40px #0008;color:#eff6ff;overflow:hidden;pointer-events:auto}
#head{height:56px;padding:12px 14px;background:#102d50;display:flex;align-items:center;justify-content:space-between;font-weight:700}
#close{background:transparent;border:0;color:white;font-size:22px;cursor:pointer}
#messages{height:calc(100% - 116px);overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:9px}
.msg{max-width:90%;padding:10px 12px;border-radius:12px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;line-height:1.45}
.user{align-self:flex-end;background:#1d4ed8;color:#fff}.bot{align-self:flex-start;background:#17314d;color:#e8f1ff}
#form{height:60px;display:flex;gap:7px;padding:9px;border-top:1px solid #294765}
#question{min-width:0;flex:1;border-radius:9px;border:1px solid #45617e;background:#102640;color:white;padding:9px;font-size:13px;outline:none}
#send{border:0;border-radius:9px;background:#2563eb;color:white;padding:0 14px;font-weight:700;cursor:pointer}
#send:disabled{opacity:.55;cursor:wait}
#hint{font-size:11px;color:#9bb2d0;padding:0 12px 6px}
</style>
</head>
<body>
<button id="launcher" aria-label="Open threat chatbot" title="Threat Intelligence Assistant">💬</button>
<section id="panel" aria-label="Threat Intelligence Chatbot">
 <header id="head"><span>🛡️ Threat Intelligence AI</span><button id="close" aria-label="Close">×</button></header>
 <div id="messages"><div class="msg bot">Hello! I'm your Threat Intelligence Assistant. Ask me about suspicious links, phishing, malware, or cyber threats.</div></div>
 <div id="hint">AI answers are for educational guidance. Verify critical threats.</div>
 <form id="form"><input id="question" autocomplete="off" placeholder="Ask about a threat..."/><button id="send" type="submit">Send</button></form>
</section>
<script>
(()=>{
 const frame=window.frameElement, launcher=document.getElementById('launcher'), panel=document.getElementById('panel');
 const messages=document.getElementById('messages'), form=document.getElementById('form'), input=document.getElementById('question'), send=document.getElementById('send');
 const api=(new URLSearchParams(location.search).get('api')||'http://localhost:8000').replace(/\/$/,'');
 function size(open){ if(frame){frame.style.position='fixed';frame.style.right='18px';frame.style.bottom='18px';frame.style.left='auto';frame.style.top='auto';frame.style.margin='0';frame.style.padding='0';frame.style.border='0';frame.style.zIndex='2147483000';frame.style.background='transparent';frame.style.width=open?'380px':'70px';frame.style.height=open?'590px':'70px';frame.style.maxWidth='calc(100vw - 20px)';frame.style.maxHeight='calc(100vh - 20px)';} }
 function add(text,who){const d=document.createElement('div');d.className='msg '+who;d.textContent=text;messages.appendChild(d);messages.scrollTop=messages.scrollHeight;}
 launcher.addEventListener('click',()=>{panel.style.display='block';launcher.style.display='none';size(true);input.focus();});
 document.getElementById('close').addEventListener('click',()=>{panel.style.display='none';launcher.style.display='block';size(false);});
 form.addEventListener('submit',async e=>{e.preventDefault();const q=input.value.trim();if(!q)return;add(q,'user');input.value='';send.disabled=true;send.textContent='…';
  try{const response=await fetch(api+'/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:q}),});if(!response.ok)throw new Error('Chat service returned HTTP '+response.status);const data=await response.json();let answer=data.llm||data.answer||'No response received.';if(data.scanner){const s=data.scanner;answer+='\n\nThreat scan: '+String(s.risk||'unknown').toUpperCase()+' risk ('+(s.score??'—')+'/100)\n'+(s.findings||[]).join('\n');}add(answer,'bot');}
  catch(err){add('Unable to connect to the chatbot. Make sure the chatbot backend is running at '+api+'. Details: '+err.message,'bot');}
  finally{send.disabled=false;send.textContent='Send';input.focus();}
 });
 size(false);
})();
</script>
</body></html>
''' , height=1, width=1, scrolling=False)
