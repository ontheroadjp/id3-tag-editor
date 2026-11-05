const files = document.querySelector('#files');
const directory = document.querySelector('#directory');
const selected = document.querySelector('#selected');
const status = document.querySelector('#status');
const audio = document.querySelector('#audio');
const labels = {title: '曲名', artist: 'アーティスト', album: 'アルバム', albumartist: 'アルバムアーティスト', date: '日付', tracknumber: 'トラック番号', genre: 'ジャンル'};
let generation = 0;

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`読み込みに失敗しました (${response.status})`);
  return response.json();
}

async function showDirectory(id = '') {
  try {
    const data = await getJson(`/api/files?directory=${encodeURIComponent(id)}`);
    directory.replaceChildren();
    if (data.parent !== null) {
      const back = document.createElement('button');
      back.textContent = '← 上のフォルダ';
      back.addEventListener('click', () => showDirectory(data.parent));
      directory.append(back);
    }
    const heading = document.createElement('p');
    heading.textContent = data.name;
    directory.append(heading);
    files.replaceChildren();
    if (!data.entries.length) files.textContent = 'フォルダまたは音声ファイルがありません。';
    for (const entry of data.entries) {
      const button = document.createElement('button');
      button.className = 'file';
      button.textContent = `${entry.directory ? '📁' : '♪'}  ${entry.name}`;
      button.addEventListener('click', () => entry.directory ? showDirectory(entry.id) : showFile(entry.id));
      files.append(button);
    }
  } catch (error) { status.textContent = error.message; }
}

async function showFile(id) {
  const current = ++generation;
  audio.pause();
  selected.hidden = true;
  status.hidden = false;
  status.textContent = '読み込み中…';
  try {
    const data = await getJson(`/api/files/${encodeURIComponent(id)}`);
    if (current !== generation) return;
    document.querySelector('#filename').textContent = data.name;
    const artwork = document.querySelector('#artwork');
    artwork.hidden = !data.artwork;
    document.querySelector('#no-artwork').hidden = data.artwork;
    artwork.src = data.artwork ? `/api/files/${encodeURIComponent(id)}/artwork` : '';
    audio.src = `/api/files/${encodeURIComponent(id)}/audio`;
    const tags = document.querySelector('#tags');
    tags.replaceChildren();
    for (const [key, label] of Object.entries(labels)) {
      const term = document.createElement('dt');
      const value = document.createElement('dd');
      term.textContent = label;
      value.textContent = data.tags[key] || '未設定';
      tags.append(term, value);
    }
    selected.hidden = false;
    status.hidden = true;
  } catch (error) { if (current === generation) status.textContent = error.message; }
}

showDirectory();
