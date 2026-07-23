"""Single-user local Flask workspace for reviewed news cards."""
from dotenv import load_dotenv

load_dotenv()

from datetime import datetime, timezone
import json
import os
from werkzeug.exceptions import HTTPException

from PIL import Image

from flask import Flask, jsonify, request, send_from_directory

from topic_monitor.cost_guard import require_paid_ai_enabled

from .cards import Draft, estimate_cost, monthly_budget_available
from .render import render_card
from .sources import fetch_article


DEFAULT_SETTINGS = {
    "model": "gpt-5.6-terra",
    "input_per_million": 2.5,
    "output_per_million": 15.0,
    "max_output_tokens": 600,
    "monthly_budget": 10.0,
}


def _response_output_text(payload):
    """Extract assistant text from the raw Responses API JSON response."""
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return content["text"]
    details = (payload.get("incomplete_details") or {}).get("reason")
    status = payload.get("status", "unknown")
    if details:
        raise ValueError("OpenAI 未完成文案生成：{0}（{1}）".format(status, details))
    raise ValueError("OpenAI 未回傳可用文案（回應狀態：{0}）".format(status))


def _payload(draft):
    payload = {
        "draft_id": draft.draft_id, "topic": draft.topic, "content": draft.content,
        "citations": draft.citations, "risk_flags": draft.risk_flags,
        "generated": draft.generated, "approved": draft.approved, "usage": draft.usage,
        "style": draft.style, "color_variant": draft.color_variant,
        "image_path": getattr(draft, "image_path", ""),
    }
    if getattr(draft, "source_name_warning", None):
        payload["source_name_warning"] = draft.source_name_warning
    return payload


def _load_settings(workspace):
    path = os.path.join(workspace, "card_settings.json")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(DEFAULT_SETTINGS, handle, ensure_ascii=False, indent=2)
    with open(path, encoding="utf-8") as handle:
        settings = dict(DEFAULT_SETTINGS)
        settings.update(json.load(handle))
        return settings


def _spent(workspace):
    record_path = os.path.join(workspace, "card_records.json")
    if not os.path.exists(record_path):
        return 0.0
    with open(record_path, encoding="utf-8") as handle:
        records = json.load(handle)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    return sum(float(row.get("usage", {}).get("actual_cost", 0)) for row in records
               if row.get("created_at", "").startswith(month))


def _append_record(workspace, draft, image_path):
    path = os.path.join(workspace, "card_records.json")
    records = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            records = json.load(handle)
    record = _payload(draft)
    record.update({"created_at": datetime.now(timezone.utc).isoformat(), "image_path": image_path})
    records.append(record)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(records, handle, ensure_ascii=False, indent=2)
    return record


def _add_usage(draft, usage, settings):
    """Accumulate actual Responses API usage across source and copy requests."""
    input_tokens = int(usage.get("input_tokens", 0))
    output_tokens = int(usage.get("output_tokens", 0))
    previous = draft.usage or {}
    total_input = int(previous.get("input_tokens", 0)) + input_tokens
    total_output = int(previous.get("output_tokens", 0)) + output_tokens
    actual_cost = (total_input / 1000000.0 * settings["input_per_million"] +
                   total_output / 1000000.0 * settings["output_per_million"])
    draft.usage = {"input_tokens": total_input, "output_tokens": total_output,
                   "actual_cost": round(actual_cost, 6), "model": settings["model"]}


def _suggest_source_name_with_openai(draft, settings):
    """Return a cautious source-name suggestion from the supplied material."""
    require_paid_ai_enabled()
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("尚未設定 OPENAI_API_KEY，無法自動判讀來源名稱")
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    citation = draft.citations[0] if draft.citations else {}
    prompt = (
        "根據下列主題、內容與網址，找出最可能的媒體或組織來源名稱。"
        "只可根據提供內容與網址判斷；若無法確認，回傳空字串。"
        "僅回傳 JSON：source_name。\n"
        "主題：{0}\n內容：{1}\n網址：{2}"
    ).format(draft.topic, draft.content[:6000], citation.get("url", ""))
    body = json.dumps({
        "model": settings["model"], "input": prompt,
        "text": {"format": {"type": "json_object"}}, "max_output_tokens": 80,
    }).encode("utf-8")
    req = Request("https://api.openai.com/v1/responses", data=body, method="POST",
                  headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            details = json.loads(error.read().decode("utf-8")).get("error", {}).get("message", str(error))
        except (ValueError, UnicodeDecodeError):
            details = str(error)
        raise ValueError("OpenAI API：" + details)
    try:
        result = json.loads(_response_output_text(payload))
        source_name = (result.get("source_name") or "").strip()
    except (ValueError, AttributeError):
        raise ValueError("AI 來源名稱回應格式不正確")
    _add_usage(draft, payload.get("usage", {}), settings)
    if not source_name:
        raise ValueError("AI 無法確認來源名稱，請手動填寫")
    return source_name


def _generate_with_openai(draft, settings):
    """Use the Responses API directly so the project remains Python 3.7 compatible."""
    require_paid_ai_enabled()
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("尚未設定 OPENAI_API_KEY")
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    prompt = ("僅根據以下資料產生繁體中文新聞資訊卡草稿。不可增加事實、數字或因果。"
              "回傳 JSON：headline(12-18字)、plain_summary(45-70字)、key_points(最多3項，每項18字內)、source_citations。\n"
              "主題：{0}\n內容：{1}\n來源：{2}").format(draft.topic, draft.content, draft.citations)
    body = json.dumps({
        "model": settings["model"], "input": prompt,
        "text": {"format": {"type": "json_object"}},
        "max_output_tokens": settings["max_output_tokens"],
    }).encode("utf-8")
    req = Request("https://api.openai.com/v1/responses", data=body, method="POST",
                  headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            details = json.loads(error.read().decode("utf-8")).get("error", {}).get("message", str(error))
        except (ValueError, UnicodeDecodeError):
            details = str(error)
        raise ValueError("OpenAI API：" + details)
    output_text = _response_output_text(payload)
    result = json.loads(output_text)
    required = ("headline", "plain_summary", "key_points", "source_citations")
    if not all(name in result for name in required) or not isinstance(result["key_points"], list):
        raise ValueError("AI 回傳內容格式不合格")
    draft.generated = result
    _add_usage(draft, payload.get("usage", {}), settings)


def create_app(workspace="data"):
    workspace = os.path.abspath(workspace)
    os.makedirs(workspace, exist_ok=True)
    app = Flask(__name__)
    drafts, settings = {}, _load_settings(workspace)

    def get_draft(draft_id):
        if draft_id not in drafts:
            raise ValueError("找不到草稿")
        return drafts[draft_id]

    def apply_source_name_suggestion(draft, user_supplied_name=False):
        """Keep a human-provided source, otherwise make one budgeted suggestion."""
        if user_supplied_name:
            return
        if not monthly_budget_available(_spent(workspace), settings["monthly_budget"]):
            draft.source_name_warning = "已達本月預算上限，未執行 AI 來源名稱判讀"
            return
        try:
            draft.citations[0]["name"] = _suggest_source_name_with_openai(draft, settings)
        except ValueError as error:
            draft.source_name_warning = str(error)

    @app.errorhandler(ValueError)
    def invalid(error):
        return jsonify({"error": str(error)}), 400

    @app.errorhandler(Exception)
    def unexpected(error):
        app.logger.exception("Unhandled workspace error")
        return jsonify({"error": "伺服器錯誤：" + str(error)}), 500

    @app.get("/")
    def index():
        return """<!doctype html><html lang='zh-Hant'><meta charset='utf-8'>

    @app.get("/favicon.ico")
    def favicon():
        return "", 204

<title>宣傳圖卡生成器</title><style>
:root{--ink:#11233d;--paper:#f5f7fa;--red:#d64545;--blue:#5b7086;--green:#1f6b5a;--gold:#c88118}body{max-width:1080px;margin:32px auto;padding:0 16px;font-family:'Microsoft JhengHei',sans-serif;color:var(--ink);background:var(--paper)}h1{margin:0}.subhead{margin:6px 0 18px;color:#526477}.status{display:inline-block;margin:10px 0 18px;padding:7px 10px;border-radius:99px;background:#e9eff6;color:#41566d;font-size:14px}.workflow{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.step-card{--step:var(--blue);--surface:#eef3f7;border:0;border-top:8px solid var(--step);border-radius:14px;padding:18px;box-shadow:0 5px 16px rgba(17,35,61,.08);transition:opacity .2s,transform .2s}.step-card.is-active{box-shadow:0 8px 22px rgba(17,35,61,.16)}.step-card.is-complete .step-kicker:after{content:'完成';margin-left:8px;padding:2px 7px;border-radius:12px;background:#fff;color:var(--step);font-size:12px}.step-card.is-locked{opacity:.48;pointer-events:none}.step-card.is-locked .step-body{display:none}.draft{--step:var(--red);--surface:#fce9e6}.review{--step:var(--blue);--surface:#e9f0f6}.preview{--step:var(--green);--surface:#e6f3ee}.export{--step:var(--gold);--surface:#fff3df}.step-kicker{font-size:13px;font-weight:bold;color:var(--step);letter-spacing:.06em}.step-card h2{margin:4px 0 12px;font-size:24px}.step-summary{margin:0 0 12px;color:#526477;font-size:14px}input,textarea{box-sizing:border-box;width:100%;padding:10px;margin:6px 0 12px;border:1px solid #cbd5e1;border-radius:7px;font:inherit;background:#fff}textarea{min-height:110px}button{background:var(--ink);color:#fff;border:0;border-radius:7px;padding:10px 16px;margin:5px 4px 5px 0;font:inherit;cursor:pointer}.secondary{background:var(--red)}.hidden{display:none}.notice{padding:10px;background:#fff;border-radius:7px;color:#41566d}.error{color:#b42318}img{max-width:100%;margin-top:12px}.style-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0}.style-choice{padding:4px;border:2px solid transparent;background:#fff;color:var(--ink)}.style-choice img{width:100%;margin:0}.style-choice.selected{border-color:var(--green)}.color-variants{margin:8px 0}.color-variants button{font-weight:bold}.color-variants button[data-color='black']{background:#111111;color:#fff}.color-variants button[data-color='red']{background:#B42318;color:#fff}.color-variants button[data-color='teal']{background:#073B4C;color:#F7F4EC}.color-variants button.selected{outline:3px solid #fff;box-shadow:0 0 0 2px var(--green)}#output img{border-radius:10px}@media(max-width:720px){.workflow{grid-template-columns:1fr}.style-grid{grid-template-columns:1fr}}</style>
<h1>宣傳圖卡生成器</h1><p class='subhead'>依序完成草稿、審核、版型預覽與輸出下載。</p><p class='notice'>宣傳內容請自行核實來源和出處。</p><p class='status'>本機工作台｜AI 功能依 token 計費，費用與月預算會在操作時提示</p>
<style>.workflow{display:grid;grid-template-columns:1fr;gap:16px}</style>
<main class='workflow'>
<section id='draft-step' class='step-card draft is-active'><div class='step-kicker'>01｜建立草稿</div><h2>輸入來源與內容</h2><div class='step-body'><p class="notice">建立草稿不會自動使用 AI。AI 功能預設停用，日後只會在你看過費用估算並明確確認後執行。</p><form id='manual'><label>主題<input name='topic' required placeholder='例如：交通提醒'></label><label>內容<textarea name='content' required placeholder='貼上欲整理的資訊內容'></textarea></label><label>來源名稱（選填）<input name='source_name' placeholder='未填時顯示「使用者提供內容」'></label><label>來源網址（選填）<input name='source_url' placeholder='https://'></label><button>建立草稿</button></form></div></section>
<section id='review-step' class='step-card review is-locked'><div class='step-kicker'>02｜文案審核</div><h2>確認資訊與文案</h2><p class='step-summary'>核對來源、主標、重點與說明內容。</p><div class='step-body'><p id='source' class='notice'></p><p>可手動填寫文案，或按「AI 產生草稿」。AI 服務未設定時，仍可手動完成。</p><label>主標<textarea id='headline' placeholder='12–18 字'></textarea></label><label>重點（每行一點，最多三點）<textarea id='points'></textarea></label><label>說明內容<textarea id='summary' placeholder='45–70 字'></textarea></label><button id='generate' type='button'>AI 產生草稿與費用預估</button><p id='message' class='notice'></p></div></section>
<section id='preview-step' class='step-card preview is-locked'><div class='step-kicker'>03｜版型預覽</div><h2>選擇圖卡風格</h2><p class='step-summary'>比較經典焦點、圓潤清新、宋體雅報與滿版色卡。</p><div class='step-body'><button id='preview' type='button'>預覽四種版型</button><div id='style-previews' class='style-grid hidden'></div><div id='color-variants' class='color-variants hidden'><button type='button' data-color='black'>黑曜</button><button type='button' data-color='red'>硃砂紅</button><button type='button' data-color='teal'>深青</button></div></div></section>
<section id='export-step' class='step-card export is-locked'><div class='step-kicker'>04｜輸出下載</div><h2>輸出已選版型</h2><p class='step-summary'>確認選擇後產生 PNG，並直接下載。</p><div class='step-body'><button id='render' class='secondary' type='button'>輸出已選版型</button><div id='output'></div><div id='actions' class='hidden'><a id='download' download>下載圖卡</a><button id='restart' type='button'>回上一頁（重新生成）</button></div></div></section>
</main>
<script>
let draftId=null;const message=t=>document.querySelector('#message').textContent=t;
function setStepState(stepId,state){const card=document.querySelector('#'+stepId);card.classList.toggle('is-locked',state==='locked');card.classList.toggle('is-active',state==='active');card.classList.toggle('is-complete',state==='complete');}
async function api(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});const d=await r.json();if(!r.ok)throw new Error(d.error||'操作失敗');return d}
function fill(d){document.querySelector('#headline').value=d.generated?.headline||'';document.querySelector('#summary').value=d.generated?.plain_summary||'';document.querySelector('#points').value=(d.generated?.key_points||[]).join('\\n')}
function showSource(d){let text='來源：'+d.citations.map(x=>x.name+'｜'+x.date).join('；');if(d.usage)text+='｜AI 來源判讀：輸入 '+d.usage.input_tokens+' token、輸出 '+d.usage.output_tokens+' token、實際費用 US$ '+d.usage.actual_cost;if(d.source_name_warning)text+='｜提醒：'+d.source_name_warning;document.querySelector('#source').textContent=text;}
document.querySelector('#manual').onsubmit=async e=>{e.preventDefault();try{const d=await api('/drafts/from-manual-content',Object.fromEntries(new FormData(e.target)));draftId=d.draft_id;setStepState('draft-step','complete');setStepState('review-step','active');showSource(d);fill(d);message(d.source_name_warning?'草稿已建立，但來源名稱請手動確認。':'草稿已建立，已自動判讀來源名稱，請確認文案。')}catch(err){alert(err.message)}};
document.querySelector('#generate').onclick=async()=>{try{const estimate=await api('/drafts/'+draftId+'/estimate');message('預估：輸入 '+estimate.input_tokens+' token、輸出 '+estimate.output_tokens+' token，費用約 US$ '+estimate.estimated_cost+'；正在生成…');const d=await api('/drafts/'+draftId+'/generate');fill(d);setStepState('preview-step','active');message('AI 草稿已產生，請仔細核對來源後再預覽版型。')}catch(err){message('無法 AI 生成：'+err.message);document.querySelector('#message').className='error'}};
document.querySelector('#render').onclick=async()=>{try{const d=await api('/drafts/'+draftId+'/approve',{headline:document.querySelector('#headline').value,plain_summary:document.querySelector('#summary').value,key_points:document.querySelector('#points').value.split('\\n').filter(Boolean).slice(0,3)});const out=await api('/drafts/'+draftId+'/render');message('圖卡已輸出。');const name=out.image_path.split(/[\\\\/]/).pop();document.querySelector('#output').innerHTML='<a href="/cards/'+encodeURIComponent(name)+'" target="_blank">開啟圖卡 PNG</a><br><img alt="宣傳圖預覽" src="/cards/'+encodeURIComponent(name)+'">'}catch(err){message(err.message);document.querySelector('#message').className='error'}};
let selectedStyle='classic',previewsReady=false;const styleNames={classic:'經典焦點',round:'圓潤清新',song:'宋體雅報'};const approveCopy=()=>api('/drafts/'+draftId+'/approve',{headline:document.querySelector('#headline').value,plain_summary:document.querySelector('#summary').value,key_points:document.querySelector('#points').value.split('\\n').filter(Boolean).slice(0,3)});document.querySelector('#preview').onclick=async()=>{try{const refreshed=await approveCopy();fill(refreshed);const styles=['classic','round','song'];const previews=await Promise.all(styles.map(style=>api('/drafts/'+draftId+'/preview',{style})));const box=document.querySelector('#style-previews');box.innerHTML=previews.map((out,index)=>'<button class="style-choice '+(styles[index]===selectedStyle?'selected':'')+'" data-style="'+styles[index]+'"><img alt="'+styleNames[styles[index]]+' 預覽" src="'+out.image_url+'"><span>'+styleNames[styles[index]]+'</span></button>').join('');box.classList.remove('hidden');box.querySelectorAll('.style-choice').forEach(button=>button.onclick=()=>{selectedStyle=button.dataset.style;box.querySelectorAll('.style-choice').forEach(item=>item.classList.toggle('selected',item===button));message('已選擇：'+styleNames[selectedStyle]);});previewsReady=true;message('請從三張預覽中選擇一種版型。')}catch(err){message(err.message);document.querySelector('#message').className='error'}};document.querySelector('#render').onclick=async()=>{try{if(!previewsReady)throw new Error('請先預覽並選擇版型');await approveCopy();const out=await api('/drafts/'+draftId+'/render',{style:selectedStyle});message('圖卡已輸出。');document.querySelector('#download').href=out.image_url;document.querySelector('#actions').classList.remove('hidden');document.querySelector('#output').innerHTML='<img alt="宣傳圖預覽" src="'+out.image_url+'">'}catch(err){message(err.message);document.querySelector('#message').className='error'}};
document.querySelector('#restart').onclick=()=>{draftId=null;document.querySelector('#manual').reset();document.querySelector('#review').classList.add('hidden');document.querySelector('#actions').classList.add('hidden');document.querySelector('#output').innerHTML='';window.scrollTo(0,0)};
const previewAction=document.querySelector('#preview').onclick;document.querySelector('#preview').onclick=async()=>{await previewAction();if(previewsReady){setStepState('review-step','complete');setStepState('preview-step','active');setStepState('export-step','active');}};
const renderAction=document.querySelector('#render').onclick;document.querySelector('#render').onclick=async()=>{await renderAction();if(document.querySelector('#download').href){setStepState('export-step','complete');}};
document.querySelector('#restart').onclick=()=>{draftId=null;previewsReady=false;document.querySelector('#manual').reset();document.querySelector('#actions').classList.add('hidden');document.querySelector('#style-previews').classList.add('hidden');document.querySelector('#output').innerHTML='';setStepState('draft-step','active');setStepState('review-step','locked');setStepState('preview-step','locked');setStepState('export-step','locked');window.scrollTo(0,0)};
let selectedColorVariant='black';styleNames.colorcard='滿版色卡';const colorNames={black:'黑曜',red:'硃砂紅',teal:'深青'};const colorVariants=document.querySelector('#color-variants');const chooseColorVariant=async variant=>{try{selectedStyle='colorcard';selectedColorVariant=variant;const out=await api('/drafts/'+draftId+'/preview',{style:'colorcard',color_variant:variant});const box=document.querySelector('#style-previews');const card=box.querySelector('[data-style="colorcard"]');if(card){card.querySelector('img').src=out.image_url;box.querySelectorAll('.style-choice').forEach(item=>item.classList.toggle('selected',item===card))}colorVariants.classList.remove('hidden');colorVariants.querySelectorAll('button').forEach(button=>button.classList.toggle('selected',button.dataset.color===variant));message('已選擇：滿版色卡｜'+colorNames[variant])}catch(err){message(err.message);document.querySelector('#message').className='error'}};const previewWithColorCard=document.querySelector('#preview').onclick;document.querySelector('#preview').onclick=async()=>{await previewWithColorCard();if(!previewsReady)return;selectedColorVariant='black';colorVariants.classList.add('hidden');const out=await api('/drafts/'+draftId+'/preview',{style:'colorcard',color_variant:selectedColorVariant});const box=document.querySelector('#style-previews');box.insertAdjacentHTML('beforeend','<button class="style-choice" data-style="colorcard"><img alt="滿版色卡 預覽" src="'+out.image_url+'"><span>滿版色卡</span></button>');box.querySelectorAll('.style-choice').forEach(button=>button.onclick=()=>{if(button.dataset.style==='colorcard'){chooseColorVariant(selectedColorVariant);return}selectedStyle=button.dataset.style;colorVariants.classList.add('hidden');box.querySelectorAll('.style-choice').forEach(item=>item.classList.toggle('selected',item===button));message('已選擇：'+styleNames[selectedStyle])});colorVariants.querySelectorAll('button').forEach(button=>button.onclick=()=>chooseColorVariant(button.dataset.color));message('請從四種版型中選擇一種；選擇滿版色卡後可再選配色。')};document.querySelector('#render').onclick=async()=>{try{if(!previewsReady)throw new Error('請先預覽並選擇版型');const styleForRender=selectedStyle;const colorForRender=styleForRender==='colorcard'?selectedColorVariant:'black';await approveCopy();const out=await api('/drafts/'+draftId+'/render',{style:styleForRender,color_variant:colorForRender});message('圖卡已輸出。');document.querySelector('#download').href=out.image_url;document.querySelector('#actions').classList.remove('hidden');document.querySelector('#output').innerHTML='<img alt="宣傳圖預覽" src="'+out.image_url+'">';setStepState('export-step','complete')}catch(err){message(err.message);document.querySelector('#message').className='error'}};const restartWithColorCard=document.querySelector('#restart').onclick;document.querySelector('#restart').onclick=()=>{selectedStyle='classic';selectedColorVariant='black';colorVariants.classList.add('hidden');restartWithColorCard()};
document.querySelector('#render').onclick=async()=>{try{if(!previewsReady)throw new Error('請先預覽並選擇版型');const selectedPreview=document.querySelector('#style-previews .style-choice.selected');const styleForRender=selectedPreview?selectedPreview.dataset.style:selectedStyle;const selectedColorButton=document.querySelector('#color-variants button.selected');const colorForRender=styleForRender==='colorcard'?(selectedColorButton?selectedColorButton.dataset.color:selectedColorVariant):'black';await approveCopy();const out=await api('/drafts/'+draftId+'/render',{style:styleForRender,color_variant:colorForRender});const renderedImageUrl=out.image_url+'?v='+Date.now();message('圖卡已輸出。');document.querySelector('#download').href=out.image_url;document.querySelector('#actions').classList.remove('hidden');document.querySelector('#output').innerHTML='<img alt="宣傳圖預覽" src="'+renderedImageUrl+'">';setStepState('export-step','complete')}catch(err){message(err.message);document.querySelector('#message').className='error'}};const refreshPreviewAction=document.querySelector('#preview').onclick;document.querySelector('#preview').onclick=async()=>{await refreshPreviewAction();const previewTimestamp=Date.now();document.querySelectorAll('#style-previews img').forEach(preview=>preview.src=preview.src.split('?')[0]+'?v='+previewTimestamp)};styleNames.image_title='圖片標題版';let imageTitleUploaded=false;document.querySelector('#preview').insertAdjacentHTML('afterend','<div id="image-title-upload" class="notice"><label>圖片標題版圖片（JPG、PNG、WebP，最大 8 MB）<input id="image-title-file" type="file" accept="image/jpeg,image/png,image/webp"></label><button id="image-title-upload-button" type="button">上傳圖片</button><span id="image-title-status"></span></div>');document.querySelector('#image-title-upload-button').onclick=async()=>{try{const file=document.querySelector('#image-title-file').files[0];if(!file)throw new Error('請先選擇圖片');if(file.size>8*1024*1024)throw new Error('圖片檔案不可超過 8 MB');const form=new FormData();form.append('image',file);const response=await fetch('/drafts/'+draftId+'/image-upload',{method:'POST',body:form});const result=await response.json();if(!response.ok)throw new Error(result.error||'圖片上傳失敗');imageTitleUploaded=true;document.querySelector('#image-title-status').textContent=' 圖片已上傳，請按「預覽四種版型」查看。'}catch(err){document.querySelector('#image-title-status').textContent=' '+err.message}};const imageTitlePreviewAction=document.querySelector('#preview').onclick;document.querySelector('#preview').onclick=async()=>{await imageTitlePreviewAction();if(!imageTitleUploaded)return;try{const out=await api('/drafts/'+draftId+'/preview',{style:'image_title'});const box=document.querySelector('#style-previews');box.insertAdjacentHTML('beforeend','<button class="style-choice" data-style="image_title"><img alt="圖片標題版 預覽" src="'+out.image_url+'?v='+Date.now()+'"><span>圖片標題版</span></button>');const card=box.querySelector('[data-style="image_title"]');card.onclick=()=>{selectedStyle='image_title';colorVariants.classList.add('hidden');box.querySelectorAll('.style-choice').forEach(item=>item.classList.toggle('selected',item===card));message('已選擇：圖片標題版')};}catch(err){message(err.message);document.querySelector('#message').className='error'}};
</script></html>"""

    @app.get("/topics")
    def topics():
        queues = sorted(name for name in os.listdir(workspace) if name.endswith("-review-queue.json"))
        if not queues:
            return jsonify([])
        with open(os.path.join(workspace, queues[-1]), encoding="utf-8") as handle:
            return jsonify(json.load(handle).get("candidates", []))

    @app.post("/drafts/from-manual-content")
    def from_manual():
        data = request.get_json(force=True)
        draft = Draft.from_manual(data.get("topic"), data.get("content"), data.get("source_name"),
                                  data.get("source_date"), data.get("source_url"))
        drafts[draft.draft_id] = draft
        return jsonify(_payload(draft)), 201

    @app.post("/drafts/from-urls")
    def from_urls():
        urls = request.get_json(force=True).get("urls", [])
        if not urls:
            raise ValueError("至少需要一個網址")
        articles = [fetch_article(url) for url in urls]
        draft = Draft.from_manual(articles[0]["title"], "\n\n".join(item["content"] for item in articles),
                                  "；".join(item["title"] for item in articles), "", articles[0]["url"])
        draft.citations = [{"name": item["title"], "date": "", "url": item["url"]} for item in articles]
        drafts[draft.draft_id] = draft
        return jsonify(_payload(draft)), 201

    @app.post("/drafts/from-topic")
    def from_topic():
        data = request.get_json(force=True)
        source_name = data.get("source_name", "")
        draft = Draft.from_manual(data.get("title"), data.get("summary") or data.get("title"),
                                  source_name or "既有待審主題", data.get("date", ""), data.get("url", ""))
        draft.risk_flags = data.get("risk_flags", [])
        drafts[draft.draft_id] = draft
        return jsonify(_payload(draft)), 201

    @app.post("/drafts/<draft_id>/estimate")
    def estimate(draft_id):
        draft = get_draft(draft_id)
        return jsonify(estimate_cost(draft.topic + "\n" + draft.content, settings, settings["max_output_tokens"]))

    @app.post("/drafts/<draft_id>/image-upload")
    def image_upload(draft_id):
        draft = get_draft(draft_id)
        upload = request.files.get("image")
        if not upload or not upload.filename:
            raise ValueError("請選擇圖片檔案")
        if upload.content_length and upload.content_length > 8 * 1024 * 1024:
            raise ValueError("圖片檔案不可超過 8 MB")
        try:
            source = Image.open(upload.stream)
            image_format = source.format
            source.verify()
            upload.stream.seek(0)
            image = Image.open(upload.stream).convert("RGB")
        except Exception:
            raise ValueError("僅支援有效的 JPG、PNG 或 WebP 圖片")
        if image_format not in ("JPEG", "PNG", "WEBP"):
            raise ValueError("僅支援 JPG、PNG 或 WebP 圖片")
        output_dir = os.path.join(workspace, "cards", "uploads")
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, draft.draft_id + ".png")
        image.save(path, "PNG")
        draft.image_path = path
        return jsonify({"image_url": "/cards/uploads/" + os.path.basename(path)}), 201

    @app.post("/drafts/<draft_id>/generate")
    def generate(draft_id):
        draft = get_draft(draft_id)
        if draft.generated:
            raise ValueError("每張圖卡最多生成一次文案")
        if not monthly_budget_available(_spent(workspace), settings["monthly_budget"]):
            raise ValueError("已達本月預算上限")
        _generate_with_openai(draft, settings)
        return jsonify(_payload(draft))

    @app.post("/drafts/<draft_id>/approve")
    def approve(draft_id):
        draft, data = get_draft(draft_id), request.get_json(force=True)
        if not draft.generated:
            required = ("headline", "plain_summary", "key_points")
            if not all(data.get(key) for key in required):
                raise ValueError("請先產生或填入完整文案")
            draft.generated = {"headline": data["headline"], "plain_summary": data["plain_summary"],
                               "key_points": data["key_points"], "source_citations": draft.citations}
        else:
            draft.generated.update({key: data[key] for key in ("headline", "plain_summary", "key_points") if key in data})
        draft.approved = True
        return jsonify(_payload(draft))

    @app.post("/drafts/<draft_id>/render")
    def render(draft_id):
        draft = get_draft(draft_id)
        if not draft.approved:
            raise ValueError("草稿尚未核准")
        style = (request.get_json(silent=True) or {}).get("style", "classic")
        if style not in ("classic", "round", "song", "colorcard", "image_title"):
            raise ValueError("不支援的圖卡風格")
        draft.style = style
        color_variant = (request.get_json(silent=True) or {}).get("color_variant", "black")
        if style == "colorcard" and color_variant not in ("black", "red", "teal"):
            raise ValueError("不支援的色卡顏色")
        draft.color_variant = color_variant if style == "colorcard" else "black"
        output_dir = os.path.join(workspace, "cards")
        if style == "colorcard":
            image_path = render_card(draft, output_dir, color_variant=draft.color_variant)
        else:
            image_path = render_card(draft, output_dir)
        record = _append_record(workspace, draft, image_path)
        return jsonify({"image_path": image_path, "image_url": "/cards/" + os.path.basename(image_path), "record": record})

    @app.post("/drafts/<draft_id>/preview")
    def preview(draft_id):
        draft = get_draft(draft_id)
        if not draft.approved:
            raise ValueError("草稿尚未核准")
        style = (request.get_json(silent=True) or {}).get("style", "classic")
        if style not in ("classic", "round", "song", "colorcard", "image_title"):
            raise ValueError("不支援的圖卡風格")
        color_variant = (request.get_json(silent=True) or {}).get("color_variant", "black")
        if style == "colorcard" and color_variant not in ("black", "red", "teal"):
            raise ValueError("不支援的色卡顏色")
        color_variant = color_variant if style == "colorcard" else "black"
        output_dir = os.path.join(workspace, "cards", "previews", style)
        if style == "colorcard":
            image_path = render_card(draft, output_dir, style_name=style, color_variant=color_variant)
        else:
            image_path = render_card(draft, output_dir, style_name=style)
        return jsonify({"image_path": image_path, "image_url": "/cards/previews/" + style + "/" + os.path.basename(image_path), "style": style, "color_variant": color_variant})

    @app.get("/cards/<path:filename>")
    def card_file(filename):
        return send_from_directory(os.path.join(workspace, "cards"), filename)

    @app.get("/stats")
    def stats():
        return jsonify({"monthly_spent": _spent(workspace), "monthly_budget": settings["monthly_budget"]})

    return app


def main():
    create_app().run(host="127.0.0.1", port=8765, debug=False)


if __name__ == "__main__":
    main()
