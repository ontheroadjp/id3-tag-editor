const files = document.querySelector('#files');
const directory = document.querySelector('#directory');
const selected = document.querySelector('#selected');
const status = document.querySelector('#status');
const audio = document.querySelector('#audio');
const labels = {title: '曲名', artist: 'アーティスト', album: 'アルバム', albumartist: 'アルバムアーティスト', date: '日付', tracknumber: 'トラック番号', genre: 'ジャンル'};
let generation = 0;
let currentFile = null;
let existingTags = {};
let existingArtwork = false;
let lookupGeneration = 0;
let lastRecordings = null;
let activeJob = null;

function cancelActiveLookup() {
  if (activeJob === null) return;
  fetch(`/api/lookups/${encodeURIComponent(activeJob)}`, {method: 'DELETE'}).catch((error) => console.warn('取得処理の中止に失敗しました。', error));
  activeJob = null;
}

function resetSelection() {
  cancelActiveLookup();
  generation++;
  lookupGeneration++;
  currentFile = null;
  lastRecordings = null;
  existingTags = {};
  existingArtwork = false;
  audio.pause();
  audio.removeAttribute('src');
  audio.load();
  document.querySelector('#candidates').replaceChildren();
  document.querySelector('#lookup-status').textContent = '';
  document.querySelector('#lookup-warning').textContent = '';
  document.querySelector('#lookup-button').disabled = false;
  selected.hidden = true;
  status.hidden = false;
  status.textContent = 'ファイルを選択してください。';
}

function renderComparison(onlineTags = {}) {
  const tags = document.querySelector('#comparison-rows');
  tags.replaceChildren();
  for (const [key, label] of Object.entries(labels)) {
    const row = document.createElement('tr');
    for (const text of [label, onlineTags[key] || '未設定', existingTags[key] || '未設定']) {
      const cell = document.createElement('td');
      cell.textContent = text;
      row.append(cell);
    }
    tags.append(row);
  }
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `読み込みに失敗しました (${response.status})`);
  }
  return response.json();
}

async function showDirectory(id = '') {
  try {
    const data = await getJson(`/api/files?directory=${encodeURIComponent(id)}`);
    directory.replaceChildren();
    if (data.parent !== null) {
      const back = document.createElement('button');
      back.textContent = '← 上のフォルダ';
      back.addEventListener('click', () => {
        resetSelection();
        showDirectory(data.parent);
      });
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
  cancelActiveLookup();
  const current = ++generation;
  lookupGeneration++;
  currentFile = null;
  lastRecordings = null;
  document.querySelector('#lookup-button').disabled = false;
  document.querySelector('#lookup-status').textContent = '';
  document.querySelector('#lookup-warning').textContent = '';
  document.querySelector('#candidates').replaceChildren();
  document.querySelector('#online-artwork').hidden = true;
  document.querySelector('#no-online-artwork').hidden = false;
  audio.pause();
  selected.hidden = true;
  status.hidden = false;
  status.textContent = '読み込み中…';
  try {
    const data = await getJson(`/api/files/${encodeURIComponent(id)}`);
    if (current !== generation) return;
    currentFile = id;
    existingTags = data.tags;
    existingArtwork = data.artwork;
    document.querySelector('#filename').textContent = data.name;
    const artwork = document.querySelector('#artwork');
    artwork.hidden = !data.artwork;
    document.querySelector('#no-artwork').hidden = data.artwork;
    artwork.src = data.artwork ? `/api/files/${encodeURIComponent(id)}/artwork` : '';
    audio.src = `/api/files/${encodeURIComponent(id)}/audio`;
    const existing = document.querySelector('#existing-artwork');
    existing.hidden = !existingArtwork;
    document.querySelector('#no-existing-artwork').hidden = existingArtwork;
    existing.src = existingArtwork ? `/api/files/${encodeURIComponent(id)}/artwork` : '';
    renderComparison();
    selected.hidden = false;
    status.hidden = true;
  } catch (error) { if (current === generation) status.textContent = error.message; }
}

async function chooseRelease(recording, release, current) {
  const lookupStatus = document.querySelector('#lookup-status');
  lookupStatus.textContent = 'リリース情報を取得中…';
  try {
    const data = await getJson(`/api/recordings/${encodeURIComponent(recording.id)}/releases/${encodeURIComponent(release.id)}`);
    if (current !== lookupGeneration) return;
    renderComparison(data);
    const image = document.querySelector('#online-artwork');
    const placeholder = document.querySelector('#no-online-artwork');
    image.hidden = true;
    placeholder.hidden = false;
    image.onload = () => { if (current === lookupGeneration) { image.hidden = false; placeholder.hidden = true; } };
    image.onerror = () => { image.hidden = true; placeholder.hidden = false; };
    image.src = data.artwork_url;
    lookupStatus.textContent = `表示中の候補: ${[release.title, release.date, release.country].filter(Boolean).join(' · ')}（別の候補も選べます）`;
  } catch (error) { if (current === lookupGeneration) lookupStatus.textContent = error.message; }
}

function normalizeAlbum(name) {
  return name.trim().normalize('NFKC').toLocaleLowerCase().replace(/\s+/g, ' ');
}

function prefersEnglish(variant) {
  const artist = variant.recording.canonical_artist || variant.recording.artist || '';
  return variant.release.language === 'eng' && /[a-z]/i.test(artist) && !/[\u3040-\u30ff\u3400-\u9fff]/.test(artist);
}

function groupCandidates(recordings, existingAlbum = '', language = 'all') {
  const groups = new Map();
  for (const recording of recordings) {
    for (const release of recording.releases) {
      const releaseLanguage = (release.language || '').toLowerCase();
      if (language !== 'all' && (language === 'unknown' ? releaseLanguage !== '' : releaseLanguage !== language)) continue;
      const name = release.title.trim();
      const key = name ? normalizeAlbum(name) : release.id;
      if (!groups.has(key)) groups.set(key, {title: name || 'アルバム名不明', variants: []});
      groups.get(key).variants.push({recording, release});
    }
  }
  const existing = normalizeAlbum(existingAlbum);
  const result = [...groups.values()];
  for (const group of result) {
    group.variants.sort((a, b) => Number(prefersEnglish(b)) - Number(prefersEnglish(a)));
  }
  return result.sort((a, b) => {
    const english = Number(prefersEnglish(b.variants[0])) - Number(prefersEnglish(a.variants[0]));
    const aMatches = existing && normalizeAlbum(a.title) === existing ? 1 : 0;
    const bMatches = existing && normalizeAlbum(b.title) === existing ? 1 : 0;
    return english || bMatches - aMatches;
  });
}

async function showCandidates(recordings, current, language = 'all') {
  const container = document.querySelector('#candidates');
  container.replaceChildren();
  const groups = groupCandidates(recordings, existingTags.album || '', language);
  if (!groups.length) {
    document.querySelector('#lookup-status').textContent = language === 'all' ? '候補が見つかりませんでした。' : '選択した言語の候補がありません。「すべて」または「言語未登録」もお試しください。';
    return;
  }
  document.querySelector('#lookup-status').textContent = 'アルバムと版を順に選択してください。';
  const steps = document.createElement('div');
  steps.className = 'selection-steps';
  const albums = document.createElement('section');
  albums.className = 'selection-step';
  const albumTitle = document.createElement('h3');
  albumTitle.textContent = '01  アルバムを選択';
  albums.append(albumTitle);
  const editions = document.createElement('section');
  editions.className = 'selection-step';
  const editionTitle = document.createElement('h3');
  editionTitle.textContent = '02  版を選択';
  const editionHint = document.createElement('p');
  editionHint.className = 'step-hint';
  editionHint.textContent = '先にアルバムを選んでください。';
  const editionList = document.createElement('div');
  editionList.className = 'edition-list';
  editionList.setAttribute('aria-label', '版の候補');
  editions.append(editionTitle, editionHint, editionList);
  steps.append(albums, editions);
  container.append(steps);
  const searchLabel = document.createElement('label');
  searchLabel.className = 'search-label';
  searchLabel.textContent = '候補を絞り込む';
  const search = document.createElement('input');
  search.type = 'search';
  search.placeholder = 'アルバム名またはアーティスト名';
  searchLabel.append(search);
  const list = document.createElement('div');
  list.className = 'candidate-list';
  list.setAttribute('aria-label', 'アルバム候補');
  list.tabIndex = 0;
  const empty = document.createElement('p');
  empty.textContent = '一致するアルバムがありません。';
  empty.hidden = true;
  albums.append(searchLabel, list, empty);
  const sorted = [...groups].sort((a, b) => a.title.localeCompare(b.title, 'ja', {numeric: true, sensitivity: 'base'}));
  const rows = [];
  for (const group of sorted) {
    const row = document.createElement('div');
    row.className = 'candidate-group';
    const primary = document.createElement('button');
    primary.className = 'candidate';
    primary.setAttribute('aria-pressed', 'false');
    const albumName = document.createElement('span');
    albumName.className = 'candidate-name';
    albumName.textContent = group.title;
    const count = document.createElement('span');
    count.className = 'candidate-count';
    count.textContent = `${group.variants.length} 版`;
    primary.append(albumName, count);
    primary.addEventListener('click', () => {
      lookupGeneration++;
      for (const item of rows) item.button.setAttribute('aria-pressed', 'false');
      primary.setAttribute('aria-pressed', 'true');
      editionList.replaceChildren();
      editionHint.textContent = `${group.title} の版を選択してください。`;
      document.querySelector('#lookup-status').textContent = '版を選ぶとタグ情報を比較できます。';
      document.querySelector('#online-artwork').hidden = true;
      document.querySelector('#no-online-artwork').hidden = false;
      renderComparison();
      const variants = [...group.variants].sort((a, b) => (a.release.date || '9999').localeCompare(b.release.date || '9999') || (a.release.country || '').localeCompare(b.release.country || ''));
      const editionButtons = [];
      for (const variant of variants) {
        const button = document.createElement('button');
        button.className = 'edition';
        button.setAttribute('aria-pressed', 'false');
        const name = document.createElement('span');
        name.className = 'edition-name';
        name.textContent = [variant.release.date || '発売日不明', variant.release.country || '地域不明'].join(' · ');
        const detail = document.createElement('span');
        detail.className = 'edition-detail';
        detail.textContent = [variant.recording.title, variant.recording.artist].filter(Boolean).join(' · ');
        button.append(name, detail);
        button.addEventListener('click', () => {
          for (const other of editionButtons) other.setAttribute('aria-pressed', 'false');
          button.setAttribute('aria-pressed', 'true');
          chooseRelease(variant.recording, variant.release, ++lookupGeneration);
        });
        editionButtons.push(button);
        editionList.append(button);
      }
    });
    row.append(primary);
    rows.push({element: row, button: primary, text: normalizeAlbum([group.title, ...group.variants.map((variant) => variant.recording.artist || '')].join(' '))});
    list.append(row);
  }
  search.addEventListener('input', () => {
    const query = normalizeAlbum(search.value);
    let visible = 0;
    for (const row of rows) {
      row.element.hidden = !row.text.includes(query);
      if (!row.element.hidden) visible++;
    }
    empty.hidden = visible !== 0;
    list.scrollTop = 0;
  });
}

function updateLanguages(recordings) {
  const select = document.querySelector('#release-language');
  const previous = select.value;
  const codes = new Set(['eng', 'jpn']);
  for (const recording of recordings) {
    for (const release of recording.releases) {
      if (release.language) codes.add(release.language.toLowerCase());
    }
  }
  select.replaceChildren();
  const languages = new Intl.DisplayNames(['ja'], {type: 'language'});
  for (const [value, label] of [['all', 'すべて'], ...[...codes].sort().map((code) => {
    try { return [code, languages.of(code) || code]; } catch { return [code, code]; }
  }), ['unknown', '言語未登録']]) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    select.append(option);
  }
  select.value = [...select.options].some((option) => option.value === previous) ? previous : 'all';
}

function changeLanguage() {
  if (lastRecordings === null) return;
  const current = ++lookupGeneration;
  document.querySelector('#online-artwork').hidden = true;
  document.querySelector('#no-online-artwork').hidden = false;
  renderComparison();
  showCandidates(lastRecordings, current, document.querySelector('#release-language').value);
}

async function startLookup() {
  if (!currentFile) return;
  cancelActiveLookup();
  const current = ++lookupGeneration;
  const button = document.querySelector('#lookup-button');
  const lookupStatus = document.querySelector('#lookup-status');
  button.disabled = true;
  document.querySelector('#candidates').replaceChildren();
  lastRecordings = null;
  document.querySelector('#lookup-warning').textContent = '';
  document.querySelector('#online-artwork').hidden = true;
  document.querySelector('#no-online-artwork').hidden = false;
  renderComparison();
  lookupStatus.textContent = '音声を照合中…';
  try {
    const response = await fetch(`/api/files/${encodeURIComponent(currentFile)}/lookup`, {method: 'POST'});
    const job = await response.json();
    if (!response.ok) throw new Error(job.detail || '取得を開始できませんでした。');
    if (current !== lookupGeneration) {
      fetch(`/api/lookups/${encodeURIComponent(job.job_id)}`, {method: 'DELETE'}).catch((error) => console.warn('取得処理の中止に失敗しました。', error));
      return;
    }
    activeJob = job.job_id;
    while (current === lookupGeneration) {
      const result = await getJson(`/api/lookups/${encodeURIComponent(job.job_id)}`);
      if (result.status === 'complete') {
        activeJob = null;
        document.querySelector('#lookup-warning').textContent = (result.warnings || []).join(' ');
        lastRecordings = result.recordings;
        updateLanguages(lastRecordings);
        await showCandidates(lastRecordings, current, document.querySelector('#release-language').value);
        break;
      }
      if (result.status === 'error') throw new Error(result.error);
      if (result.status === 'cancelled') throw new Error('取得処理を中止しました。');
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  } catch (error) { if (current === lookupGeneration) lookupStatus.textContent = error.message; }
  finally { if (current === lookupGeneration) { activeJob = null; button.disabled = false; } }
}

document.querySelector('#lookup-button').addEventListener('click', startLookup);
document.querySelector('#release-language').addEventListener('change', changeLanguage);
showDirectory();
