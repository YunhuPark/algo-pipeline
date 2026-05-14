
let lbImages=[],lbIndex=0;
function _renderLb(){
  const w=document.getElementById('lbContentWrapper');
  const src=lbImages[lbIndex];
  if(src.toLowerCase().endsWith('.mp4')){
    w.innerHTML=`<video class="lb-img" src="${src}" controls autoplay loop muted playsinline></video>`;
  }else{
    w.innerHTML=`<img class="lb-img" src="${src}">`;
  }
}
function openLightbox(src,all,idx){lbImages=all||[src];lbIndex=idx||0;document.getElementById('lightbox').classList.add('active');document.getElementById('lbCounter').textContent=(lbIndex+1)+' / '+lbImages.length;document.body.style.overflow='hidden';_renderLb();}
function closeLightbox(){document.getElementById('lightbox').classList.remove('active');document.body.style.overflow='';document.getElementById('lbContentWrapper').innerHTML='';}
function navLightbox(d){lbIndex=(lbIndex+d+lbImages.length)%lbImages.length;document.getElementById('lbCounter').textContent=(lbIndex+1)+' / '+lbImages.length;_renderLb();}
document.getElementById('lightbox').addEventListener('click',e=>{if(e.target.id==='lightbox')closeLightbox();});
document.addEventListener('keydown',e=>{if(!document.getElementById('lightbox').classList.contains('active'))return;if(e.key==='Escape')closeLightbox();if(e.key==='ArrowLeft')navLightbox(-1);if(e.key==='ArrowRight')navLightbox(1);});
