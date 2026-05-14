
let currentJobId = null;
let currentDirName = null;

function startJob(topic, auto, reels) {
  document.getElementById('progressPanel').style.display = 'block';
  document.getElementById('previewPanel').style.display = 'none';
  document.getElementById('logBox').innerHTML = '';
  document.getElementById('progressBadge').textContent = '실행 중';
  document.getElementById('progressBadge').className = 'badge badge-pending';
  document.getElementById('progressTitle').textContent = auto ? '자동 뉴스 선택 + 생성 중...' : `"${topic}" 생성 중...`;

  fetch('/generate/start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({topic, auto, reels})
  })
  .then(r => r.json())
  .then(data => {
    currentJobId = data.job_id;
    listenSSE(currentJobId);
  });
}

function listenSSE(jobId) {
  const evtSource = new EventSource(`/generate/stream/${jobId}`);
  const logBox = document.getElementById('logBox');

  evtSource.addEventListener('log', e => {
    const line = document.createElement('div');
    line.textContent = e.data;
    if (e.data.match(/^\[[\d.]+\]|완료!|✓/)) {
      line.className = 'log-step';
    } else if (e.data.match(/✓|passed|통과/i)) {
      line.className = 'log-done';
    } else if (e.data.match(/오류|error|fail/i)) {
      line.className = 'log-err';
    }
    logBox.appendChild(line);
    logBox.scrollTop = logBox.scrollHeight;
  });

  evtSource.addEventListener('topic', e => {
    document.getElementById('progressTitle').textContent = `"${e.data}" 생성 중...`;
  });

  evtSource.addEventListener('done', e => {
    evtSource.close();
    const info = JSON.parse(e.data);
    currentDirName = info.image_dir;
    document.getElementById('progressBadge').textContent = '완료';
    document.getElementById('progressBadge').className = 'badge badge-published';
    document.querySelector('#progressPanel .progress-fill').style.animation = 'none';
    document.querySelector('#progressPanel .progress-fill').style.opacity = '1';
    showPreview(info.image_dir, info.topic, info.count, info.filenames);
  });

  evtSource.addEventListener('error', e => {
    evtSource.close();
    document.getElementById('progressBadge').textContent = '오류';
    document.getElementById('progressBadge').className = 'badge badge-skipped';
    const line = document.createElement('div');
    line.className = 'log-err';
    line.textContent = '✕ 오류: ' + e.data;
    logBox.appendChild(line);
  });
}

function showPreview(dirName, topic, count, filenames) {
  document.getElementById('previewPanel').style.display = 'block';
  document.getElementById('previewTitle').textContent = `✓ "${topic}" 완료 — ${count}장`;
  const grid = document.getElementById('cardGrid');
  grid.innerHTML = '';
  
  const safeFilenames = filenames || [];
  
  for (let i = 1; i <= count; i++) {
    const num = String(i).padStart(2, '0');
    const fname = safeFilenames.find(f => f.startsWith(`card_${num}_`));
    const types = ['cover', 'content', 'content', 'content', 'content', 'cta'];
    const t = types[i-1] || 'content';
    const finalName = fname || `card_${num}_${t}.png`;
    
    const src = `/output_img/${dirName}/${finalName}`;
    const isVideo = finalName.toLowerCase().endsWith('.mp4');
    
    const div = document.createElement('div');
    div.className = 'card-thumb';
    
    if (isVideo) {
      div.innerHTML = `<video src="${src}" autoplay loop muted playsinline style="width:100%;height:100%;object-fit:cover;border-radius:var(--radius-sm);background:#000;"></video>
        <div class="num-badge">${i}/${count}</div>`;
    } else {
      div.innerHTML = `<img src="${src}" onerror="this.src='/output_img/${dirName}/card_${num}.png'">
        <div class="num-badge">${i}/${count}</div>`;
    }
    
    div.addEventListener('click', () => {
      const mediaNodes = Array.from(grid.querySelectorAll('img, video'));
      const allSrcs = mediaNodes.map(m => m.src);
      openLightbox(mediaNodes[i-1].src, allSrcs, i-1);
    });
    grid.appendChild(div);
  }

  // caption 로드
  fetch(`/caption/${dirName}`)
    .then(r => r.text())
    .then(txt => {
      if (txt) {
        document.getElementById('captionBox').style.display = 'block';
        document.getElementById('captionBox').textContent = txt;
      }
    });
}

document.getElementById('manualForm').addEventListener('submit', e => {
  e.preventDefault();
  const topic = document.getElementById('topicInput').value.trim();
  if (!topic) return;
  const reels = document.getElementById('makeReels').checked;
  startJob(topic, false, reels);
});

document.getElementById('autoBtn').addEventListener('click', () => {
  const reels = document.getElementById('autoReels').checked;
  startJob('', true, reels);
});

document.getElementById('publishBtn').addEventListener('click', () => {
  if (!currentDirName) return;
  if (!confirm('Instagram에 바로 발행하시겠습니까?')) return;
  const btn = document.getElementById('publishBtn');
  btn.textContent = '발행 중...';
  btn.disabled = true;
  fetch('/publish_now', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({dir_name: currentDirName})
  })
  .then(r => r.json())
  .then(data => {
    if (data.success) {
      btn.textContent = '✓ 발행 완료!';
      btn.className = 'btn btn-success';
      alert('Instagram 발행 완료!\npost_id: ' + data.post_id);
    } else {
      btn.textContent = '📤 Instagram 발행';
      btn.disabled = false;
      alert('발행 실패: ' + data.error);
    }
  });
});

document.getElementById('newGenBtn').addEventListener('click', () => {
  document.getElementById('progressPanel').style.display = 'none';
  document.getElementById('previewPanel').style.display = 'none';
  document.getElementById('topicInput').value = '';
  window.scrollTo(0, 0);
});
