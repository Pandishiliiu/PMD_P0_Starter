'use strict';
const $=id=>document.getElementById(id);
const state={tab:'device',calibrations:[],runs:[],calId:null,runId:null,job:null,ipad:null,camera:null,patIndex:1};
const post=async(url,body)=>api(url,body===undefined?{method:'POST'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
async function api(url,opt={}){const res=await fetch(url,opt);let val;const ct=res.headers.get('content-type')||'';try{val=ct.includes('json')?await res.json():await res.text();}catch(e){val={detail:'无法解析服务器响应'};}
if(!res.ok)throw new Error(val?.detail||`请求失败 ${res.status}`);return val;}
function notice(message,isError=false){let node=$('notice');node.textContent=String(message);node.className=isError?'error':'';node.style.display='block';clearTimeout(notice.timer);notice.timer=setTimeout(()=>node.style.display='none',6200);}
async function action(fn){try{await fn();}catch(e){notice(e.message||e,true);}}
function fmt(v,d=2){return v===undefined||v===null||!Number.isFinite(Number(v))?'—':Number(v).toFixed(d);}
function escapeHTML(t){return String(t??'').replace(/[&<>"']/g,a=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[a]));}
function bind(id,cb){$(id).addEventListener('click',()=>action(cb));}
function selectedRun(){return state.runs.find(x=>x.id===state.runId);}
function selectedCal(){return state.calibrations.find(x=>x.id===state.calId);}

// Manual aperture is owned by the selected measurement, never by calibration.
// It is drawn over that run's actual white frame (same pixel coordinates).
const roiEdit={runId:null, image:null, draft:null, saved:null, points:[],
               dragging:false, start:null, fullW:1,fullH:1};
function roiCopy(v){return v?JSON.parse(JSON.stringify(v)):null;}
function clamp01(v){return Math.max(0,Math.min(1,v));}
function roiPointer(e){
  const r=$('roiCanvas').getBoundingClientRect();
  return [clamp01((e.clientX-r.left)/r.width),clamp01((e.clientY-r.top)/r.height)];
}
function roiShapePath(ctx,shape,w,h){
  if(shape?.mode==='ellipse'){
    let [x0,y0,x1,y1]=shape.box;
    ctx.ellipse((x0+x1)*w/2,(y0+y1)*h/2,
       Math.max(1,(x1-x0)*w/2),Math.max(1,(y1-y0)*h/2),0,0,Math.PI*2);
  }else if(shape?.mode==='polygon'){
    shape.points.forEach(([x,y],i)=>i?ctx.lineTo(x*w,y*h):ctx.moveTo(x*w,y*h));
    ctx.closePath();
  }
}
function roiRedraw(){
  const cv=$('roiCanvas');if(!roiEdit.image)return;
  const ctx=cv.getContext('2d'),w=cv.width,h=cv.height;
  ctx.clearRect(0,0,w,h);ctx.drawImage(roiEdit.image,0,0,w,h);
  if(roiEdit.draft){
    ctx.save();ctx.beginPath();ctx.rect(0,0,w,h);
    roiShapePath(ctx,roiEdit.draft,w,h);
    ctx.fillStyle='rgba(4,12,24,.53)';ctx.fill('evenodd');
    ctx.beginPath();roiShapePath(ctx,roiEdit.draft,w,h);
    ctx.strokeStyle='#f7bb44';ctx.lineWidth=Math.max(2,w/500);ctx.stroke();
    ctx.restore();
  }
  if(roiEdit.points.length){
    ctx.beginPath();roiEdit.points.forEach(([x,y],i)=>i?ctx.lineTo(x*w,y*h):ctx.moveTo(x*w,y*h));
    ctx.strokeStyle='#f7bb44';ctx.lineWidth=Math.max(2,w/550);ctx.stroke();
    for(const [x,y] of roiEdit.points){ctx.beginPath();ctx.arc(x*w,y*h,4,0,2*Math.PI);
      ctx.fillStyle='#f7bb44';ctx.fill();}
  }
  const dirty=JSON.stringify(roiEdit.draft)!==JSON.stringify(roiEdit.saved)||roiEdit.points.length>0;
  $('roiStatus').textContent=roiEdit.saved?'已保存掩膜'+(dirty?' · 未保存修改':''):
                                     '自动检测'+(dirty?' · 未保存修改':'');
  $('roiInfo').textContent=`原图 ${roiEdit.fullW} × ${roiEdit.fullH} · 仅保留圈选内部`+
    (roiEdit.saved ? ` · 掩膜占比 ${fmt(roiEdit.saved.coverage*100,1)}%` : '');
}
async function roiLoadForRun(run){
  const id=run.id;
  roiEdit.runId=id;roiEdit.image=null;roiEdit.draft=null;roiEdit.saved=null;
  roiEdit.points=[];roiEdit.dragging=false;
  $('roiStatus').textContent='读取白图中…';
  try{
    const info=await api(`/api/measurements/${encodeURIComponent(id)}/roi`);
    if(roiEdit.runId!==id)return;
    if(!info.available)throw new Error('请先完成采集或导入原始图片');
    const image=new Image();
    image.src=`/api/measurements/${encodeURIComponent(id)}/roi/preview?t=${Date.now()}`;
    await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(new Error('白图预览加载失败'));});
    if(roiEdit.runId!==id)return;
    roiEdit.image=image;roiEdit.fullW=info.width;roiEdit.fullH=info.height;
    roiEdit.saved=roiCopy(info.roi);roiEdit.draft=roiCopy(info.roi);
    $('roiMode').value=info.roi?.mode||'ellipse';
    $('roiInset').value=info.roi?.inset_px??12;
    const canvas=$('roiCanvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;
    roiRedraw();
  }catch(e){if(roiEdit.runId===id){$('roiStatus').textContent='白图不可用';notice('掩膜编辑：'+e.message,true);}}
}
function roiCheckEditor(){if(!state.runId||roiEdit.runId!==state.runId||!roiEdit.image)
  throw new Error('请先选择已采集的测量，等待白图加载');}
function roiModeHelp(){
  $('roiHelp').textContent=$('roiMode').value==='ellipse'?
    '在白图上拖出椭圆，沿镜面工作区内缘选择，避开外围反射环。':
    '沿工作区内缘依次单击边界点，点击「闭合多边形」，再保存。';
}
$('roiMode').addEventListener('change',()=>{
  roiEdit.draft=null;roiEdit.points=[];roiModeHelp();roiRedraw();
});
$('roiCanvas').addEventListener('pointerdown',e=>{
  if(!roiEdit.image||roiEdit.runId!==state.runId)return;
  const p=roiPointer(e);
  if($('roiMode').value==='polygon'){
    roiEdit.draft=null;roiEdit.points.push(p);roiRedraw();
  }else{
    roiEdit.points=[];roiEdit.start=p;roiEdit.dragging=true;
    e.currentTarget.setPointerCapture(e.pointerId);
  }
});
$('roiCanvas').addEventListener('pointermove',e=>{
  if(!roiEdit.dragging)return;
  const p=roiPointer(e),s=roiEdit.start;
  roiEdit.draft={mode:'ellipse',box:[Math.min(s[0],p[0]),Math.min(s[1],p[1]),
                                Math.max(s[0],p[0]),Math.max(s[1],p[1])]};
  roiRedraw();
});
$('roiCanvas').addEventListener('pointerup',e=>{
  if(!roiEdit.dragging)return;
  roiEdit.dragging=false;roiRedraw();
});
$('roiCanvas').addEventListener('pointercancel',()=>{roiEdit.dragging=false;});
bind('roiFinish',async()=>{
  roiCheckEditor();if(roiEdit.points.length<3)throw new Error('至少需要三个顶点');
  roiEdit.draft={mode:'polygon',points:roiEdit.points.slice()};
  roiEdit.points=[];roiRedraw();
});
bind('roiUndo',async()=>{
  roiCheckEditor();
  if(roiEdit.points.length)roiEdit.points.pop();
  else roiEdit.draft=null;
  roiRedraw();
});
bind('roiClear',async()=>{roiCheckEditor();roiEdit.draft=null;roiEdit.points=[];roiRedraw();});
bind('roiSave',async()=>{
  roiCheckEditor();if(!roiEdit.draft)throw new Error('请先圈出镜面有效区域');
  const id=state.runId;const run=selectedRun();
  if(run?.decoded&&!confirm('修改掩膜将使已有解码、重建结果失效。旧结果会归档，继续？'))return;
  const payload={...roiCopy(roiEdit.draft),inset_px:+$('roiInset').value};
  const out=await post(`/api/measurements/${encodeURIComponent(id)}/roi`,payload);
  roiEdit.saved=roiCopy(out.roi);roiEdit.draft=roiCopy(out.roi);roiRedraw();
  await refreshData();notice('掩膜已保存到本次测量。点击「相位解码」重新计算。');
});
bind('roiView',async()=>{roiCheckEditor();if(!roiEdit.saved)throw new Error('请先保存手动掩膜');
  window.open(`/api/measurements/${encodeURIComponent(state.runId)}/file/roi_mask.png?t=${Date.now()}`,'_blank');
});
bind('roiReset',async()=>{
  roiCheckEditor();const id=state.runId;const run=selectedRun();
  if(run?.decoded&&!confirm('恢复自动掩膜会使当前计算结果失效，旧结果将归档。继续？'))return;
  await post(`/api/measurements/${encodeURIComponent(id)}/roi/reset`);
  roiEdit.saved=null;roiEdit.draft=null;roiEdit.points=[];roiRedraw();
  await refreshData();notice('已取消手动掩膜；后续解码使用自动Mask。');
});

function switchTab(tab){state.tab=tab;document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));
 document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.id==='tab-'+tab));syncCameraStream();}
document.querySelectorAll('nav button').forEach(b=>b.addEventListener('click',()=>switchTab(b.dataset.tab)));

function patternDraw(){const s=state.ipad;if(!s)return;const plan=s.plan||[];const p=plan[state.patIndex]||plan[0];if(!p)return;
const canvas=$('patternPreview'),ctx=canvas.getContext('2d'),W=canvas.width,H=canvas.height,im=ctx.createImageData(W,H),config=s.config;
let row=new Uint8ClampedArray(p.type==='fringe'?(p.axis==='x'?W:H):1);
if(p.type==='black')row[0]=0;
else if(p.type==='white')row[0]=255;
else for(let i=0;i<row.length;i++)row[i]=config.brightness+config.contrast*Math.cos(2*Math.PI*(p.freq*i/row.length+p.phase/4));
let a=im.data,off=0;
for(let y=0;y<H;y++)for(let x=0;x<W;x++){
let v=row[p.type!=='fringe'?0:(p.axis==='x'?x:y)];a[off++]=v;a[off++]=v;a[off++]=v;a[off++]=255;}
ctx.putImageData(im,0,0);}
function updatePlan(s){let old=state.ipad?.plan?.map(x=>x.name).join(',');let fresh=s.plan.map(x=>x.name).join(',');state.ipad=s;
if(old!==fresh){$('patternSelect').innerHTML='';s.plan.forEach((p,i)=>{let o=document.createElement('option');o.value=i;o.textContent=`${i}  ${p.name}`;$('patternSelect').append(o);});}
state.patIndex=s.index;$('patternSelect').value=String(s.index);patternDraw();
$('ipadDetail').textContent=`iPad：${s.connected?'已连接':'离线'}  ·  ${s.browser?.width??'—'}×${s.browser?.height??'—'} 像素  ·  图案 ${s.index+1}/${s.plan.length}`;
}
async function refreshSystem(){const sys=await api('/api/system');state.camera=sys.camera;
$('ipStatus').textContent='iPad '+(sys.ipad.connected?'● 已连接':'○ 离线');
$('camStatus').textContent='相机 '+(sys.camera.connected?'● 已连接':'○ 未连接');
$('modeBadge').textContent=sys.demo?'模拟模式（不是真实测量）':'';
$('frameInfo').textContent=`预览：${sys.camera.preview?'运行':'停止'}  ·  ${sys.camera.preview?fmt(sys.camera.preview_fps,1)+' fps  ·  ':''}曝光 ${fmt(sys.camera.exposure_us,0)} µs`;
syncCameraStream();
$('cameraDetail').textContent=sys.camera.last_error||`连接：${sys.camera.connected?'是':'否'}  /  自动采集：${sys.camera.acquiring?'运行中':'空闲'}`;
updatePlan(sys.ipad);
}
function quickPattern(axis,freq){const s=state.ipad;if(!s)return;let index=s.plan.findIndex(p=>p.axis===axis&&p.freq===freq&&p.phase===0);
if(index>=0)return patternSelect(index);notice('当前频率列表没有这个图案',true);}
async function patternSelect(index){await post('/api/patterns/select',{index});await refreshSystem();}
bind('patBlack',()=>patternSelect(0));bind('patWhite',()=>patternSelect(1));
bind('patX4',()=>quickPattern('x',4));bind('patY4',()=>quickPattern('y',4));
bind('patShow',()=>patternSelect(+$('patternSelect').value));
$('patternSelect').addEventListener('change',()=>{state.patIndex=+$('patternSelect').value;patternDraw();});
bind('patApply',async()=>{
 const freqs=$('freqInput').value.split(/[\s,，]+/).filter(Boolean).map(Number);
 await post('/api/patterns/config',{freqs,brightness:+$('brightness').value,contrast:+$('contrast').value,
 screen_width_mm:+$('screenW').value,screen_height_mm:+$('screenH').value});
 await refreshSystem();notice('显示设置已应用');});
bind('patCheck',async()=>{await refreshSystem();let s=state.ipad;
notice(s.connected?`iPad在线，画布 ${s.browser.width}×${s.browser.height}，${s.ack_rev===s.rev?'图案已渲染':'等待图案渲染'}`:
'iPad离线：请在iPad中访问 http://电脑局域网IP:8000/ ，关闭旧服务后仅运行本程序。',!s.connected);});

bind('camConnect',async()=>{await post('/api/camera/connect');await refreshSystem();notice('相机已连接');});
bind('camDisconnect',async()=>{await post('/api/camera/disconnect');await refreshSystem();});
bind('camExposure',async()=>{await post('/api/camera/exposure',{us:+$('exposure').value});await refreshSystem();notice('曝光已设置');});
bind('camPreview',async()=>{await post('/api/camera/preview',{on:true});await refreshSystem();});
bind('camPreviewOff',async()=>{await post('/api/camera/preview',{on:false});await refreshSystem();});
bind('camSnapshot',async()=>{const out=await post('/api/camera/snapshot');notice(`已保存测试图片：${out.file}`);
window.open('/api/camera/snapshot/'+encodeURIComponent(out.file),'_blank');});
// Each MJPEG response contains successive frames. No repeated HTTP polling.
function syncCameraStream(){
 const el=$('cameraFrame');if(!el)return;
 const want=state.tab==='camera' && !!state.camera?.preview;
 if(want && el.dataset.live!=='1'){
   el.dataset.live='1';
   el.src='/api/camera/stream?ts='+Date.now();
 }else if(!want && el.dataset.live==='1'){
   el.dataset.live='0';
   el.removeAttribute('src');
 }
}


async function refreshData(){let [cs,rs]=await Promise.all([api('/api/calibrations'),api('/api/measurements')]);
 state.calibrations=cs;state.runs=rs;
 if(!state.calId&&cs.length)state.calId=cs[0].id;
 if(!state.runId&&rs.length)state.runId=rs[0].id;
 renderCals();renderRuns();}
function renderCals(){let e=$('calList');e.innerHTML='';
 for(let c of state.calibrations){let b=document.createElement('button');b.className='record'+(c.id===state.calId?' active':'');
 b.innerHTML=`${escapeHTML(c.name)}<small>${escapeHTML(c.created_at)} · ${c.ready?'已锁定':'待完成'}</small>`;
 b.onclick=()=>{state.calId=c.id;renderCals();};e.append(b);}
 const list=$('runCal'),old=list.value;list.innerHTML='<option value="">选择已有标定</option>';
 for(let c of state.calibrations.filter(c=>c.ready)){let o=document.createElement('option');o.value=c.id;o.textContent=c.name+' / '+c.id.slice(0,13);list.append(o);}
 if([...list.options].some(o=>o.value===old))list.value=old;
 else if(list.options.length>1)list.selectedIndex=1;
 let cal=selectedCal();$('calControls').hidden=!cal;$('calEmpty').hidden=!!cal;
 $('calSelected').textContent=cal?.id??'';
 if(cal){
 if(state.intrinsicShownCal!==cal.id){
   state.intrinsicShownCal=cal.id;
   const b=cal.intrinsic_quality?.board||{type:'checkerboard',cols:cal.board_inner?.[0]||9,rows:cal.board_inner?.[1]||6,pitch_mm:cal.square_mm||4};
   $('intrBoardType').value=b.type||'checkerboard';
   $('intrCols').value=b.cols;$('intrRows').value=b.rows;$('intrPitch').value=b.pitch_mm;
   $('intrFixK3').checked=cal.intrinsic_quality?.fix_k3??true;
 }
 let p=cal.plane;let q=cal.geometry_quality;
 let info=[`状态：${cal.ready?'可复用 · 已锁定':'待完成'}`,`内参：${cal.intrinsics?'已有':'未导入'}`,
 `棋盘格：${cal.board_inner.join('×')} 内角点，${cal.square_mm} mm/格`,
 `屏幕：${cal.screen_width_mm}×${cal.screen_height_mm} mm`,
 p?`参考平面 d = ${fmt(p.d,3)} mm；PnP RMS ${fmt(p.median_pnp_rms_px,2)} px；有效图 ${p.num_used}/${p.num_valid}`:'参考平面：未计算',
 q?`iPad标定：${q.mode==='imported'?'导入已有参数':`重投影中位数 ${fmt(q.median_reprojection_px,2)} px；内点率 ${fmt(q.inlier_fraction*100,1)}%`}`:'iPad位姿：未计算',
 q?.warning||'',
 p?.boards?.length? '棋盘格每张：\n'+p.boards.map(b=>`${b.file}: ${b.ok?'d='+fmt(b.d,2)+' mm  RMS='+fmt(b.rms_px,2)+' px'+(b.accepted?' [保留]':' [排除]'):'角点失败'}`).join('\n'):'' ];
 $('intrCount').textContent='照片数：读取中…';
 const iq=cal.intrinsic_quality;
 if(iq){
   const rows=[`图像：${iq.num_used}/${iq.num_detected}/${iq.num_photos} (拟合/识别/总数)`,
   `RMS：${fmt(iq.rms_px,3)} px  ·  分辨率：${iq.image_size.join('×')}`,
   `fx=${fmt(iq.fx,2)}  fy=${fmt(iq.fy,2)}  cx=${fmt(iq.cx,2)}  cy=${fmt(iq.cy,2)}`,
   `畸变：${(iq.dist||[]).map(v=>fmt(v,5)).join(', ')}`,
   `倾角跨度：${fmt(iq.quality?.tilt_range_deg,1)}°  ·  最大板面占比：${fmt((iq.quality?.max_board_area_fraction||0)*100,1)}%`,
   ...(iq.quality?.warnings||[]).map(x=>'注意：'+x),
   ...(iq.photos||[]).map(x=>`${x.file}: ${x.detected?(x.accepted?'使用':'排除')+'  RMS '+fmt(x.rms_px,2)+' px':'识别失败 '+(x.reason||'')}`)];
   $('intrResult').textContent=rows.join('\n');
 }else $('intrResult').textContent='尚未计算。';
 loadIntrinsicPhotos(cal.id).catch(e=>$('intrCount').textContent='照片读取失败：'+e.message);
 $('calDetail').textContent=info.filter(Boolean).join('\n');
 document.querySelectorAll('#calControls button').forEach(b=>{b.disabled=!!cal.ready&&!['openCalFolder','intrDownload','intrPreview'].includes(b.id);});
 }
}
function renderRuns(){let e=$('runList');e.innerHTML='';
 for(let r of state.runs){let b=document.createElement('button');b.className='record'+(r.id===state.runId?' active':'');
 b.innerHTML=`${escapeHTML(r.name)}<small>${escapeHTML(r.created_at)} · ${escapeHTML(r.status)}${r.source==='demo'?' [模拟]':''}</small>`;
 b.onclick=()=>{state.runId=r.id;renderRuns();};e.append(b);}
 let run=selectedRun();$('runControls').hidden=!run;$('runEmpty').hidden=!!run;
 $('runSelected').textContent=run?.id??'';
 if(!run){$('roiPanel').hidden=true;roiEdit.runId=null;return;}
 $('roiPanel').hidden=!run.captured;
 if(run.captured && roiEdit.runId!==run.id){roiLoadForRun(run).catch(e=>notice(e.message,true));}
 if(!run.captured && roiEdit.runId===run.id){roiEdit.runId=null;}
 $('runDetail').textContent=`状态：${run.status}  ·  来源：${run.source==='demo'?'模拟数据（非真实实验）':run.source}  ·  压力：${run.pressure||'未记录'}  ·  标定：${run.calibration_name}\n数据单元：${run.id}\n标定参数已复制快照；不受标定库后续修改影响。`;
 $('captureRun').disabled=!!run.captured;$('importRun').disabled=!!run.captured;
 $('decodeRun').disabled=!run.captured;
 $('reconstructRun').disabled=!run.decoded;
 $('curvatureRun').disabled=!run.reconstructed;
 const d=run.decode, r=run.reconstruction;
 let box=$('runResults');box.innerHTML='';
 if(d){let p=document.createElement('p');p.className='quiet';p.textContent=`解码有效：${fmt(d.valid_fraction*100,1)}% · 亮区覆盖：${fmt(d.coverage_of_bright_area*100,1)}%`;box.append(p);
  let img=document.createElement('img');img.src=`/api/measurements/${run.id}/file/decoded/mask.png?t=${Date.now()}`;img.alt='Mask';box.append(img);}
 if(r){let m=document.createElement('div');m.className='metrics';
 let vals=[['PV · 去平面',fmt(r.detrended_pv_um,2)+' µm'],['RMS · 去平面',fmt(r.detrended_rms_um,2)+' µm'],
 ['球面半径',r.sphere?fmt(r.sphere.radius_mm,2)+' mm':'未拟合'],['球面残差 RMS',r.sphere?fmt(r.sphere.rms_um,2)+' µm':'—'],
 ['有效点',String(r.points)],['X × Y',fmt(r.extent_x_mm,1)+' × '+fmt(r.extent_y_mm,1)+' mm']];
 m.innerHTML=vals.map(([label,v])=>`<div class="metric"><b>${escapeHTML(v)}</b><small>${escapeHTML(label)}</small></div>`).join('');box.append(m);
 for(let file of ['height_detrended.png','height_3d.png','sphere_residual.png']){
  let img=document.createElement('img');img.alt=file;img.src=`/api/measurements/${run.id}/file/reconstruction/${file}?t=${Date.now()}`;
  if(file==='sphere_residual.png'&&!r.sphere)continue;box.append(img);
 }
 if(run.curvature){
  const c=run.curvature;let p=document.createElement('div');p.className='metrics';
  const d=c.principal||[],a=c.aperture||{};
  const fields=[['主曲率 R₁',d[0]?fmt(d[0].radius_mm,1)+' mm':'—'],
    ['主曲率 R₂',d[1]?fmt(d[1].radius_mm,1)+' mm':'—'],
    ['长轴方向 R',fmt(a.radius_along_major_mm,1)+' mm'],
    ['短轴方向 R',fmt(a.radius_along_minor_mm,1)+' mm'],
    ['主曲率相对差',fmt(c.principal_radius_relative_difference_percent,1)+'%'],
    ['二次拟合 RMS',fmt(c.quadratic_vertical_rms_um,2)+' µm'],
    ['长轴方向角',fmt(a.major_axis_angle_deg_from_x,1)+'°'],
    ['主曲率方向角',d[0]?fmt(d[0].axis_angle_deg_from_x,1)+'°':'—']];
  p.innerHTML=fields.map(([label,value])=>`<div class="metric"><b>${escapeHTML(value)}</b><small>${escapeHTML(label)}</small></div>`).join('');
  box.append(p);
  let pic=document.createElement('img');pic.alt='二次曲面残差';
  pic.src=`/api/measurements/${run.id}/file/reconstruction/quadratic_residual.png?t=${Date.now()}`;box.append(pic);
  let doc=document.createElement('p');doc.className='quiet';
  doc.textContent='曲率为重建面形的局部估计；工作区椭圆不代表镜面必为椭球面。';box.append(doc);
 }
 let link=document.createElement('p');link.innerHTML=`<a target="_blank" href="/api/measurements/${run.id}/file/reconstruction/points.csv">导出点云CSV</a>　·　<a target="_blank" href="/api/measurements/${run.id}/file/reconstruction/shape.npz">NPZ数据</a>　·　<a target="_blank" href="/api/measurements/${run.id}/file/reconstruction/result.json">结果JSON</a>`;box.append(link);
 }
}
async function createCal(){let c=await post('/api/calibrations',{name:$('calName').value,board_inner:[+$('boardX').value,+$('boardY').value],square_mm:+$('boardMM').value,
 screen_width_mm:+$('screenW').value,screen_height_mm:+$('screenH').value});state.calId=c.id;await refreshData();notice('标定已建立');}
bind('createCal',createCal);
bind('createRun',async()=>{let cal=$('runCal').value;if(!cal)throw new Error('请先选择已完成的标定');
let r=await post('/api/measurements',{name:$('runName').value,calibration_id:cal,pressure:$('pressure').value,notes:$('runNotes').value});
state.runId=r.id;await refreshData();notice('测量数据单元已创建，并已固定标定快照');});

async function upload(endpoint,file,field='file'){
 if(!file)throw new Error('未选中文件');let form=new FormData();form.append(field,file);
 return api(endpoint,{method:'POST',body:form});}
async function uploadMany(endpoint,files){if(!files?.length)throw new Error('请先选择图片');let form=new FormData();for(let f of files)form.append('files',f);
return api(endpoint,{method:'POST',body:form});}
function requireCal(){if(!state.calId)throw new Error('请先选择标定');return state.calId;}
function requireRun(){if(!state.runId)throw new Error('请先选择测量');return state.runId;}
async function loadIntrinsicPhotos(id){
 const v=await api(`/api/calibrations/${encodeURIComponent(id)}/intrinsics/photos`);
 if(state.calId===id)$('intrCount').textContent=`当前照片：${v.count}张，建议18–25张（不同位置、距离、倾角）`;
}
bind('intrCapture',async()=>{
 const id=requireCal();const r=await post(`/api/calibrations/${id}/intrinsics/capture`);
 await loadIntrinsicPhotos(id);notice(`已拍摄 ${r.file}；改变板的倾斜、位置或距离再拍下一张`);
});
$('intrUpload').onclick=()=>$('intrFiles').click();
$('intrFiles').onchange=()=>action(async()=>{
 const id=requireCal();let r=await uploadMany(`/api/calibrations/${id}/intrinsics/photos/upload`,$('intrFiles').files);
 await loadIntrinsicPhotos(id);notice(`已导入 ${r.saved.length} 张`);$('intrFiles').value='';
});
bind('intrSolve',async()=>{
 const id=requireCal();const payload={board:{type:$('intrBoardType').value,
 cols:+$('intrCols').value, rows:+$('intrRows').value, pitch_mm:+$('intrPitch').value},
 fix_k3:$('intrFixK3').checked};
 await startJob(`/api/calibrations/${id}/intrinsics/solve`,payload);
});
bind('intrPreview',async()=>{
 const id=requireCal(); const c=selectedCal();
 const photo=(c?.intrinsic_quality?.photos||[]).find(p=>p.detected)?.file;
 if(!photo)throw new Error('请先计算内参，生成角点预览');
 const imageName=photo.replace(/\.[^.]+$/,'.jpg');
 window.open(`/api/calibrations/${id}/intrinsics/annotation/${encodeURIComponent(imageName)}`,'_blank');
});
bind('intrDownload',async()=>{
 const id=requireCal();
 window.open(`/api/calibrations/${id}/intrinsics/download`,'_blank');
});
$('calIntrBtn').onclick=()=>$('calIntrFile').click();
$('calGeoImport').onclick=()=>$('calGeoFile').click();
$('calBoardUpload').onclick=()=>$('calBoardFiles').click();
$('calFlatImport').onclick=()=>$('calFlatFile').click();
$('importRun').onclick=()=>$('importRunFile').click();
$('calIntrFile').onchange=()=>action(async()=>{await upload(`/api/calibrations/${requireCal()}/intrinsics`,$('calIntrFile').files[0]);await refreshData();notice('内参已导入');});
$('calGeoFile').onchange=()=>action(async()=>{await upload(`/api/calibrations/${requireCal()}/geometry/import`,$('calGeoFile').files[0]);await refreshData();notice('完整标定已导入并锁定');});
$('calBoardFiles').onchange=()=>action(async()=>{let r=await uploadMany(`/api/calibrations/${requireCal()}/board/upload`,$('calBoardFiles').files);notice('已导入棋盘图片：'+r.saved.length);});
$('calFlatFile').onchange=()=>action(async()=>{await upload(`/api/calibrations/${requireCal()}/flat/import`,$('calFlatFile').files[0]);await refreshData();notice('平面镜相移图已导入');});
$('importRunFile').onchange=()=>action(async()=>{await upload(`/api/measurements/${requireRun()}/raw/import`,$('importRunFile').files[0]);await refreshData();notice('原始相移图已导入');});
bind('calBoardPhoto',async()=>{let r=await post(`/api/calibrations/${requireCal()}/board/capture`);notice(`已保存 ${r.filename}。改变棋盘位置时务必保持镜面共面。`);});
bind('calPlaneSolve',async()=>{let r=await post(`/api/calibrations/${requireCal()}/board/solve`);await refreshData();notice(`参考平面 d = ${fmt(r.d,2)} mm`);});
bind('calSolve',async()=>{let r=await post(`/api/calibrations/${requireCal()}/solve`);await refreshData();notice(`iPad几何标定完成；重投影中位数 ${fmt(r.geometry.median_reprojection_px,2)} px`);});

async function startJob(url,body){const j=await post(url,body);state.job=j.id;$('jobLine').hidden=false;await refreshData();}
bind('calFlatCapture',()=>startJob(`/api/calibrations/${requireCal()}/flat/capture`));
bind('captureRun',()=>startJob(`/api/measurements/${requireRun()}/capture`));
bind('decodeRun',()=>startJob(`/api/measurements/${requireRun()}/decode`));
bind('curvatureRun',async()=>{if(!state.runId)throw new Error('先选择测量');
  await post(`/api/measurements/${state.runId}/curvature`);
  await refreshData();notice('双向曲率分析完成，已保存到本次测量');
});
bind('reconstructRun',()=>startJob(`/api/measurements/${requireRun()}/reconstruct`,{anchor_mm:+$('anchorHeight').value}));
bind('openRunFolder',async()=>{await post(`/api/measurements/${requireRun()}/open-folder`);});
bind('openCalFolder',async()=>{await post(`/api/calibrations/${requireCal()}/open-folder`);});
async function pollJob(){if(!state.job)return;try{let j=await api('/api/jobs/'+state.job);$('jobTitle').textContent=j.label;
$('jobMessage').textContent=j.message;$('jobBar').value=j.progress;
if(j.status!=='running'){state.job=null;setTimeout(()=>$('jobLine').hidden=true,2200);await refreshData();
 if(j.status==='error')notice(j.error,true);else notice('任务完成');}}
catch(e){notice(e.message,true);state.job=null;}}
setInterval(()=>{pollJob().catch(()=>{});},650);
setInterval(()=>{refreshSystem().catch(()=>{});},1700);
async function main(){await Promise.all([refreshSystem(),refreshData()]);$('freqInput').value=state.ipad.config.freqs.join(',');
$('brightness').value=state.ipad.config.brightness;$('contrast').value=state.ipad.config.contrast;
$('screenW').value=state.ipad.config.screen_width_mm;$('screenH').value=state.ipad.config.screen_height_mm;
$('exposure').value=state.camera.exposure_us;}
main().catch(e=>notice('后端连接失败：'+e.message,true));
