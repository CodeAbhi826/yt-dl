// Parse URL params
const params = new URLSearchParams(location.search);
const type = params.get('type');
const title = params.get('title');
const videoTitle = params.get('video');
const meta = params.get('meta');
const thumb = params.get('thumb');
const jobId = params.get('jobId');

document.getElementById('title').textContent = title || '';
document.getElementById('videoTitle').textContent = videoTitle || '';
document.getElementById('meta').textContent = meta || '';
document.getElementById('toast').classList.add('v-' + type);

if (thumb) {
  document.getElementById('thumb').src = thumb;
} else {
  document.getElementById('thumb').style.display = 'none';
}

if (type === 'completed') {
  document.getElementById('app').innerHTML = 'yt-dl <span class="badge badge-green">&#10003;</span>';
}

if (type === 'failed') {
  document.getElementById('retryBtn').style.display = '';
}

function doRetry() {
  if (jobId) {
    fetch('http://127.0.0.1:5000/api/jobs/' + jobId + '/retry', { method: 'POST' }).catch(() => {});
  }
  window.close();
}

// Auto dismiss (failed persists longer)
const timeout = type === 'failed' ? 8000 : 3000;
setTimeout(() => {
  document.getElementById('toast').style.transition = 'opacity 300ms ease-out, transform 300ms ease-out';
  document.getElementById('toast').style.opacity = '0';
  document.getElementById('toast').style.transform = 'scale(.96)';
  setTimeout(() => window.close(), 300);
}, timeout);
