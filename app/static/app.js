const files = document.querySelector('#files');
const directory = document.querySelector('#directory');
const selected = document.querySelector('#selected');
const status = document.querySelector('#status');
const audio = document.querySelector('#audio');
const labels = {title: '曲名', artist: 'アーティスト', album: 'アルバム', albumartist: 'アルバムアーティスト', date: '日付', tracknumber: 'トラック番号', discnumber: 'ディスク番号', genre: 'ジャンル', comment: 'コメント'};
let generation = 0;
let currentFile = null;
let existingTags = {};
let existingValues = {};
let existingArtwork = false;
let lookupGeneration = 0;
let lastRecordings = null;
let activeJob = null;
let baselineTags = {};
let draftTags = {};
let selectedFields = new Set();
let fileVersion = '';
let writeEnabled = false;
let comments = [];
let selectedComment = null;
let baselineComment = null;
let fileFormat = '';
let existingArtworks = {};
let onlineImages = {front: null, back: null};
let onlineUrls = {front: null, back: null};
let artworkChoices = {};
let artworkBaseline = {};
let artworkUploads = {};
let artworkTargets = {};
let artworkTargetBaseline = {};
let selectedArtwork = new Set();
let operationBusy = false;
let pendingArtworkLoads = 0;
let selectedLanguage = 'all';
let blockingHistory = false;

function cancelActiveLookup() {
  if (activeJob === null) return;
  fetch(`/api/lookups/${encodeURIComponent(activeJob)}`, {method: 'DELETE'}).catch((error) => console.warn('取得処理の中止に失敗しました。', error));
  activeJob = null;
}

function resetSelection() {
  clearOnlineImage('front');
  clearOnlineBack();
  baselineTags = {};
  draftTags = {};
  selectedFields.clear();
  selectedArtwork.clear();
  artworkChoices = {};
  artworkBaseline = {};
  artworkUploads = {};
  artworkTargets = {};
  artworkTargetBaseline = {};
  selectedComment = null;
  baselineComment = null;
  blockingHistory = false;
  cancelActiveLookup();
  generation++;
  lookupGeneration++;
  currentFile = null;
  lastRecordings = null;
  existingTags = {};
  existingValues = {};
  existingArtwork = false;
  audio.pause();
  audio.removeAttribute('src');
  audio.load();
  document.querySelector('#candidates').replaceChildren();
  document.querySelector('#lookup-status').textContent = '';
  document.querySelector('#lookup-warning').textContent = '';
  document.querySelector('#lookup-button').disabled = false;
  document.querySelector('#refresh-existing').hidden = true;
  document.querySelector('#save-status').textContent = '';
  selected.hidden = true;
  status.hidden = false;
  status.textContent = 'ファイルを選択してください。';
}

function setDraft(values) {
  baselineTags = Object.fromEntries(Object.keys(labels).map((key) => [key, values[key] || '']));
  if (values === existingTags && comments.length > 1) baselineTags.comment = '';
  draftTags = {...baselineTags};
  selectedFields.clear();
  selectedComment = comments.length === 1 ? 0 : null;
  baselineComment = selectedComment;
  resetArtworkState();
}

function resetArtworkState() {
  const sides = fileFormat === 'mp3' ? ['front', 'back'] : ['cover'];
  artworkChoices = Object.fromEntries(sides.map((side) => [side, side === 'cover' ? 'front' : side]));
  artworkBaseline = {...artworkChoices};
  artworkUploads = {};
  artworkTargets = Object.fromEntries(sides.map((side) => [side, (existingArtworks[side] || []).length === 1 || side === 'cover' && (existingArtworks[side] || []).length > 0 ? 0 : null]));
  artworkTargetBaseline = {...artworkTargets};
  selectedArtwork.clear();
}

function artworkCandidate(side) {
  const choice = artworkChoices[side];
  if (choice === 'delete') return {delete: true};
  if (choice === 'upload') return artworkUploads[side] || null;
  return onlineImages[choice] || null;
}

function artworkChanged(side) {
  const candidate = artworkCandidate(side);
  const items = existingArtworks[side] || [];
  const target = artworkTargets[side] === null ? null : items[artworkTargets[side]];
  if (!candidate || (fileFormat === 'mp3' && items.length > 1 && !target)) return false;
  if (candidate.delete) return items.length > 0 && (fileFormat !== 'mp3' || Boolean(target));
  return !target || candidate.sha256 !== target.sha256 || (side === 'cover' && items.length > 1);
}

function currentExisting(key) {
  if (key === 'comment' && selectedComment !== null) return comments[selectedComment]?.value || '';
  return existingTags[key] || '';
}

function fieldChanged(key) {
  return (key !== 'comment' && (existingValues[key] || []).length > 1) || draftTags[key] !== currentExisting(key);
}

function updateEditActions() {
  const changed = [...selectedFields].some(fieldChanged) ||
    [...selectedArtwork].some(artworkChanged);
  const missingComment = selectedFields.has('comment') && comments.length > 1 && selectedComment === null;
  const missingArtwork = [...selectedArtwork].some((side) => !artworkCandidate(side) ||
    (fileFormat === 'mp3' && (existingArtworks[side] || []).length > 1 && artworkTargets[side] === null));
  document.querySelector('#save-tags').disabled = operationBusy || pendingArtworkLoads > 0 || blockingHistory || !writeEnabled || !changed || missingComment || missingArtwork;
  document.querySelector('#reset-tags').disabled = operationBusy || !hasPendingEdits();
  document.querySelector('#select-all').disabled = operationBusy;
  document.querySelector('#clear-all').disabled = operationBusy;
}

function renderComparison() {
  const tags = document.querySelector('#comparison-rows');
  tags.replaceChildren();
  for (const [key, label] of Object.entries(labels)) {
    const row = document.createElement('tr');
    const heading = document.createElement('td');
    heading.textContent = label;
    const inputCell = document.createElement('td');
    const input = document.createElement('input');
    input.type = 'text';
    input.disabled = operationBusy;
    input.value = draftTags[key] || '';
    input.setAttribute('aria-label', `${label}の新しい値`);
    input.addEventListener('input', () => { draftTags[key] = input.value; updateEditActions(); });
    inputCell.append(input);
    if (key === 'comment' && comments.length > 1) {
      const select = document.createElement('select');
      select.disabled = operationBusy;
      select.setAttribute('aria-label', '編集するコメント');
      const placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.textContent = '編集するコメントを選択';
      select.append(placeholder);
      comments.forEach((comment, index) => {
        const option = document.createElement('option');
        option.value = String(index);
        option.textContent = [comment.language, comment.description, comment.value].filter(Boolean).join(' · ');
        select.append(option);
      });
      select.value = selectedComment === null ? '' : String(selectedComment);
      select.addEventListener('change', () => {
        selectedComment = select.value === '' ? null : Number(select.value);
        renderComparison();
      });
      inputCell.append(select);
    }
    const toggleCell = document.createElement('td');
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.disabled = operationBusy;
    toggle.textContent = selectedFields.has(key) ? '⇒' : '';
    toggle.className = 'field-toggle';
    toggle.setAttribute('aria-label', `${label}を反映対象にする`);
    toggle.setAttribute('aria-pressed', String(selectedFields.has(key)));
    toggle.addEventListener('click', () => {
      if (selectedFields.has(key)) selectedFields.delete(key); else selectedFields.add(key);
      renderComparison();
    });
    toggleCell.append(toggle);
    const existing = document.createElement('td');
    const values = key === 'comment' ? [] : (existingValues[key] || []);
    if (values.length > 1) {
      values.forEach((value) => { const item = document.createElement('div'); item.textContent = value; existing.append(item); });
    } else {
      existing.textContent = currentExisting(key) || '未設定';
    }
    row.append(heading, inputCell, toggleCell, existing);
    tags.append(row);
  }
  updateEditActions();
}

async function artworkData(blob) {
  if (!['image/png', 'image/jpeg'].includes(blob.type) || blob.size > 10 * 1024 * 1024) {
    throw new Error('10 MiB以下のJPEGまたはPNG画像を選んでください。');
  }
  const bytes = await blob.arrayBuffer();
  const hash = await crypto.subtle.digest('SHA-256', bytes);
  const sha256 = [...new Uint8Array(hash)].map((value) => value.toString(16).padStart(2, '0')).join('');
  const encoded = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.onerror = () => reject(new Error('画像を読み込めませんでした。'));
    reader.readAsDataURL(blob);
  });
  return {data: encoded, mime: blob.type, sha256};
}

function renderArtworkControls() {
  const container = document.querySelector('#artwork-controls');
  container.replaceChildren();
  const sides = fileFormat === 'mp3' ? ['front', 'back'] : ['cover'];
  for (const side of sides) {
    const section = document.createElement('div');
    section.className = 'artwork-edit';
    const title = document.createElement('h3');
    title.textContent = side === 'front' ? '表' : side === 'back' ? '裏' : '画像（1枚）';
    section.append(title);
    const items = existingArtworks[side] || [];
    if (items.length) {
      const gallery = document.createElement('div');
      gallery.className = 'artwork-gallery';
      items.forEach((item, index) => {
        const image = document.createElement('img');
        image.alt = `既存画像 ${index + 1}${item.description ? `: ${item.description}` : ''}`;
        image.src = `/api/files/${encodeURIComponent(currentFile)}/artwork?side=${side === 'back' ? 'back' : 'front'}&index=${index}&v=${encodeURIComponent(fileVersion)}`;
        gallery.append(image);
      });
      section.append(gallery);
    }
    if (fileFormat === 'mp3' && items.length > 1) {
      const target = document.createElement('select');
      target.disabled = operationBusy;
      target.setAttribute('aria-label', `${title.textContent}の変更対象`);
      const placeholder = document.createElement('option');
      placeholder.value = ''; placeholder.textContent = '変更する既存画像を選択'; target.append(placeholder);
      items.forEach((item, index) => {
        const option = document.createElement('option'); option.value = String(index);
        option.textContent = `${index + 1}: ${item.description || '説明なし'}`; target.append(option);
      });
      target.value = artworkTargets[side] === null ? '' : String(artworkTargets[side]);
      target.addEventListener('change', () => {
        artworkTargets[side] = target.value === '' ? null : Number(target.value);
        renderArtworkControls(); updateEditActions();
      });
      section.append(target);
    }
    const source = document.createElement('select');
    source.disabled = operationBusy;
    source.setAttribute('aria-label', `${title.textContent}の画像ソース`);
    const options = side === 'cover' ? [['front', '取得画像（表）'], ['back', '取得画像（裏）']] : [[side, '取得画像']];
    options.push(['upload', '画像ファイル'], ['delete', '画像を削除']);
    for (const [value, label] of options) {
      const option = document.createElement('option'); option.value = value; option.textContent = label; source.append(option);
    }
    source.value = artworkChoices[side];
    source.addEventListener('change', () => { artworkChoices[side] = source.value; renderArtworkControls(); updateEditActions(); });
    section.append(source);
    const file = document.createElement('input');
    file.type = 'file'; file.accept = 'image/jpeg,image/png';
    file.disabled = operationBusy;
    file.setAttribute('aria-label', `${title.textContent}の画像ファイル`);
    file.addEventListener('change', async () => {
      if (!file.files?.length) return;
      const current = generation;
      pendingArtworkLoads++;
      updateEditActions();
      try {
        const image = await artworkData(file.files[0]);
        if (current !== generation) return;
        artworkUploads[side] = image;
        artworkChoices[side] = 'upload';
        renderArtworkControls(); updateEditActions();
      } catch (error) { document.querySelector('#save-status').textContent = error.message; }
      finally { pendingArtworkLoads--; updateEditActions(); }
    });
    section.append(file);
    if (artworkChoices[side] === 'upload' && artworkUploads[side]) {
      const preview = document.createElement('img');
      preview.className = 'artwork-preview';
      preview.alt = `${title.textContent}の新しい画像`;
      preview.src = `data:${artworkUploads[side].mime};base64,${artworkUploads[side].data}`;
      section.append(preview);
    }
    const toggle = document.createElement('button');
    toggle.type = 'button'; toggle.className = 'field-toggle';
    toggle.textContent = selectedArtwork.has(side) ? '⇒' : '';
    toggle.setAttribute('aria-label', `${title.textContent}の画像を反映対象にする`);
    toggle.setAttribute('aria-pressed', String(selectedArtwork.has(side)));
    toggle.disabled = operationBusy || (!selectedArtwork.has(side) && !artworkChanged(side));
    toggle.addEventListener('click', () => {
      if (selectedArtwork.has(side)) selectedArtwork.delete(side); else selectedArtwork.add(side);
      renderArtworkControls(); updateEditActions();
    });
    section.append(toggle);
    container.append(section);
  }
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(typeof body.detail === 'string' ? body.detail : `読み込みに失敗しました (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

async function postJson(url, method, body) {
  const response = await fetch(url, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const result = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = result.detail;
    const message = typeof detail === 'string' ? detail : detail?.message || `操作に失敗しました (${response.status})`;
    const error = new Error(message);
    error.status = response.status;
    error.detail = detail;
    throw error;
  }
  return result;
}

async function refreshExisting(keepDraft = true) {
  const data = await getJson(`/api/files/${encodeURIComponent(currentFile)}`);
  existingTags = data.tags;
  existingValues = data.tag_values || {};
  comments = data.comments || [];
  existingArtworks = data.artworks || {};
  fileVersion = data.version;
  writeEnabled = data.write_enabled;
  for (const selector of ['#player-panel', '#lookup-panel', '#online', '#artwork-panel']) document.querySelector(selector).hidden = false;
  document.querySelector('#recovery-status').textContent = '';
  status.hidden = true;
  const front = document.querySelector('#existing-artwork');
  front.hidden = !data.artwork;
  document.querySelector('#no-existing-artwork').hidden = data.artwork;
  front.src = data.artwork ? `/api/files/${encodeURIComponent(currentFile)}/artwork?v=${encodeURIComponent(fileVersion)}` : '';
  const back = document.querySelector('#existing-back-artwork');
  back.hidden = !data.back_artwork;
  document.querySelector('#no-existing-back-artwork').hidden = data.back_artwork;
  back.src = data.back_artwork ? `/api/files/${encodeURIComponent(currentFile)}/artwork?side=back&v=${encodeURIComponent(fileVersion)}` : '';
  artworkTargets = Object.fromEntries(Object.keys(artworkChoices).map((side) => {
    const count = (existingArtworks[side] || []).length;
    return [side, count === 1 || side === 'cover' && count > 0 ? 0 : null];
  }));
  artworkTargetBaseline = {...artworkTargets};
  selectedComment = comments.length === 1 ? 0 : null;
  baselineComment = selectedComment;
  if (!keepDraft) setDraft(existingTags);
  renderComparison();
  renderArtworkControls();
}

async function saveTags() {
  if (!currentFile || document.querySelector('#save-tags').disabled) return;
  const status = document.querySelector('#save-status');
  const updates = Object.fromEntries([...selectedFields].filter(fieldChanged).map((key) => [key, draftTags[key]]));
  const multiple = Object.keys(updates).filter((key) => key !== 'comment' && (existingValues[key] || []).length > 1);
  if (multiple.length && !window.confirm(`${multiple.map((key) => labels[key]).join('、')}の複数の値を1つの値に置き換えます。続けますか？`)) return;
  const payload = {expected_version: fileVersion, updates};
  const artwork = {};
  for (const side of selectedArtwork) {
    if (!artworkChanged(side)) continue;
    const candidate = artworkCandidate(side);
    const target = (existingArtworks[side] || [])[artworkTargets[side]];
    artwork[side] = {data: candidate.delete ? null : candidate.data, mime: candidate.delete ? null : candidate.mime};
    if (fileFormat === 'mp3' && target) artwork[side].description = target.description;
  }
  if (Object.keys(artwork).length) payload.artwork = artwork;
  if (fileFormat === 'm4a' && artwork.cover && (existingArtworks.cover || []).length > 1) {
    const message = artwork.cover.data === null ? '既存の複数画像をすべて削除します。続けますか？' : '既存の複数画像が1枚に置き換わります。続けますか？';
    if (!window.confirm(message)) return;
    payload.confirm_multiple_artwork = true;
  }
  if (selectedFields.has('comment') && selectedComment !== null) {
    if (fileFormat === 'mp3') payload.comment_ref = [comments[selectedComment].language, comments[selectedComment].description];
    else payload.comment_index = selectedComment;
  }
  operationBusy = true;
  renderComparison();
  renderArtworkControls();
  let committed = false;
  try {
    const result = await postJson(`/api/files/${encodeURIComponent(currentFile)}/tags`, 'POST', payload);
    committed = true;
    baselineTags = {...draftTags};
    selectedFields.clear();
    await refreshExisting();
    resetArtworkState();
    renderArtworkControls();
    status.textContent = result.temporary_copy_remaining ? '保存しました。一時コピーが残っています。履歴を確認してください。' : '保存しました。';
    await loadHistory();
  } catch (error) {
    status.textContent = committed ? '保存は完了しましたが、結果を再読み込みできませんでした。' :
      error.detail?.recovered === true ? `${error.message} 元のタグを復旧しました。` :
      error.detail?.recovered === false ? `${error.message} 自動復旧に失敗しました。履歴を確認してください。` : error.message;
    if (error.status === 409 || committed || typeof error.detail?.recovered === 'boolean') document.querySelector('#refresh-existing').hidden = false;
    await loadHistory();
  } finally {
    operationBusy = false;
    renderComparison();
    renderArtworkControls();
  }
}

async function loadHistory() {
  const container = document.querySelector('#history');
  const current = generation;
  const fileId = currentFile;
  container.replaceChildren();
  if (!currentFile) return;
  blockingHistory = true;
  updateEditActions();
  try {
    const entries = await getJson(`/api/files/${encodeURIComponent(fileId)}/history`);
    if (current !== generation) return;
    blockingHistory = entries.some((entry) => ['pending', 'failed_unrecovered'].includes(entry.result) || entry.recovery_name);
    if (blockingHistory) document.querySelector('#save-status').textContent = '未完了または復旧できなかった処理があります。履歴を確認してください。';
    updateEditActions();
    loadPendingRecoveries();
    if (!entries.length) { container.textContent = 'バックアップはありません。'; return; }
    const table = document.createElement('table');
    const header = document.createElement('tr');
    for (const title of ['ID', '日時', 'ステータス', '変更したタグ', '操作']) {
      const cell = document.createElement('th'); cell.textContent = title; header.append(cell);
    }
    table.append(header);
    for (const entry of entries) {
      const row = document.createElement('tr');
      const artworkLabels = {artwork_front: 'アートワーク（表）', artwork_back: 'アートワーク（裏）', artwork_cover: 'アートワーク'};
      const resultLabels = {pending: '未完了', not_applied: '書き込み前に中止', failed_recovered: '失敗・復旧済み', failed_unrecovered: '失敗・復旧できず', interrupted_restored: '復元済み'};
      const result = (resultLabels[entry.result] ? `・${resultLabels[entry.result]}` : '') +
        (entry.recovery_name ? `・一時コピー: ${entry.recovery_name}` : '');
      const values = [`#${entry.id}`, new Date(entry.created_at).toLocaleString('ja-JP'),
        `${entry.operation === 'restore' ? `復元（#${entry.restored_from}）` : '新規'}${result}`,
        entry.operation === 'restore' ? 'タグ全体' : entry.fields.map((field) => labels[field] || artworkLabels[field] || field).join('、')];
      for (const value of values) { const cell = document.createElement('td'); cell.textContent = value; row.append(cell); }
      const actions = document.createElement('td');
      const recoveryCopy = entry.recovery_state === 'ready' && ['pending', 'failed_unrecovered'].includes(entry.result);
      const restore = document.createElement('button'); restore.textContent = recoveryCopy ? '復旧' : '復元';
      restore.disabled = !writeEnabled;
      if (entry.recovery_state === 'copying') {
        restore.disabled = true;
        restore.title = '未完了の一時コピーを先に確認してください。';
      }
      if (entry.snapshot_format?.includes('damaged-region')) {
        restore.disabled = true;
        restore.title = '破損時の記録のため、この状態には復元できません。';
      }
      restore.addEventListener('click', async () => {
        const message = recoveryCopy ? `#${entry.id} の一時コピーでファイル全体を復旧します。` : `#${entry.id} の直前のタグ全体を復元します。以降の変更も戻ります。`;
        if (!confirmDiscardEdits() || !window.confirm(message)) return;
        operationBusy = true;
        renderComparison(); renderArtworkControls();
        let committed = false;
        try {
          const endpoint = recoveryCopy ? 'recover' : 'restore';
          const result = await postJson(`/api/files/${encodeURIComponent(fileId)}/history/${entry.id}/${endpoint}`, 'POST', {expected_version: fileVersion});
          committed = true;
          selectedFields.clear();
          await refreshExisting(false);
          document.querySelector('#save-status').textContent = result.temporary_copy_remaining ? '復元しました。一時コピーが残っています。履歴を確認してください。' : recoveryCopy ? '復旧しました。' : '復元しました。';
          await loadHistory();
        } catch (error) {
          const message = committed ? '復元は完了しましたが、結果を再読み込みできませんでした。' :
            error.detail?.recovered === true ? `${error.message} 元のタグを復旧しました。` :
            error.detail?.recovered === false ? `${error.message} 自動復旧に失敗しました。` : error.message;
          document.querySelector('#save-status').textContent = message;
          document.querySelector('#recovery-status').textContent = message;
          if (error.status === 409 || committed || typeof error.detail?.recovered === 'boolean') document.querySelector('#refresh-existing').hidden = false;
          await loadHistory();
        } finally { operationBusy = false; renderComparison(); renderArtworkControls(); }
      });
      const remove = document.createElement('button'); remove.textContent = '削除';
      remove.disabled = !writeEnabled;
      if (entry.recovery_name) { remove.disabled = true; remove.title = '先に一時コピーを確認してください。'; }
      remove.addEventListener('click', async () => {
        if (operationBusy || pendingArtworkLoads) return;
        const warning = ['pending', 'failed_unrecovered'].includes(entry.result) ? '未完了の処理を復旧するために必要な可能性があります。' : '';
        if (!window.confirm(`${warning}バックアップ #${entry.id} を削除しますか？`)) return;
        operationBusy = true; renderComparison(); renderArtworkControls();
        try { await postJson(`/api/files/${encodeURIComponent(fileId)}/history/${entry.id}`, 'DELETE', {}); await loadHistory(); }
        catch (error) { document.querySelector('#save-status').textContent = error.message; }
        finally { operationBusy = false; renderComparison(); renderArtworkControls(); }
      });
      actions.append(restore, remove);
      const canCleanup = entry.recovery_state === 'copying' && ['pending', 'not_applied'].includes(entry.result) ||
        entry.recovery_state === 'ready' && ['completed', 'not_applied', 'failed_recovered', 'interrupted_restored'].includes(entry.result);
      if (canCleanup) {
        const cleanup = document.createElement('button'); cleanup.textContent = '一時コピー削除'; cleanup.disabled = !writeEnabled;
        cleanup.addEventListener('click', async () => {
          if (operationBusy || pendingArtworkLoads || !window.confirm('一時コピーを検証して削除しますか？')) return;
          operationBusy = true; renderComparison(); renderArtworkControls();
          try { await postJson(`/api/files/${encodeURIComponent(fileId)}/history/${entry.id}/cleanup`, 'POST', {}); await loadHistory(); }
          catch (error) { document.querySelector('#recovery-status').textContent = error.message; }
          finally { operationBusy = false; renderComparison(); renderArtworkControls(); }
        });
        actions.append(cleanup);
      }
      row.append(actions); table.append(row);
    }
    container.append(table);
  } catch (error) { if (current === generation) container.textContent = error.message; }
}

async function loadPendingRecoveries() {
  const container = document.querySelector('#pending-recoveries');
  try {
    const entries = await getJson('/api/recovery/pending');
    if (!Array.isArray(entries)) throw new Error('未完了処理の一覧が不正です。');
    container.replaceChildren();
    container.hidden = entries.length === 0;
    if (!entries.length) return;
    const title = document.createElement('h2');
    title.textContent = '確認が必要な処理';
    container.append(title);
    for (const entry of entries) {
      const row = document.createElement('p');
      const label = document.createElement('span');
      label.textContent = `#${entry.id} ${entry.path}（${entry.recovery_state === 'copying' ? 'コピー作成が未完了' : entry.recovery_name ? '一時コピーあり' : '処理が未完了'}） `;
      const button = document.createElement('button');
      button.type = 'button'; button.textContent = '確認';
      button.addEventListener('click', () => { if (confirmDiscardEdits()) showFile(entry.identifier); });
      row.append(label, button); container.append(row);
    }
  } catch (error) {
    container.hidden = false;
    container.textContent = `未完了処理を確認できません: ${error.message}`;
  }
}

async function showDirectory(id = '') {
  try {
    const data = await getJson(`/api/files?directory=${encodeURIComponent(id)}`);
    directory.replaceChildren();
    if (data.parent !== null) {
      const back = document.createElement('button');
      back.textContent = '← 上のフォルダ';
      back.addEventListener('click', () => {
        if (!confirmDiscardEdits()) return;
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
      button.addEventListener('click', () => {
        if (!confirmDiscardEdits()) return;
        if (entry.directory) { resetSelection(); showDirectory(entry.id); } else showFile(entry.id);
      });
      files.append(button);
    }
  } catch (error) { status.textContent = error.message; }
}

async function showFile(id) {
  clearOnlineImage('front');
  clearOnlineBack();
  cancelActiveLookup();
  const current = ++generation;
  lookupGeneration++;
  currentFile = null;
  blockingHistory = false;
  document.querySelector('#refresh-existing').hidden = true;
  document.querySelector('#save-status').textContent = '';
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
    for (const selector of ['#player-panel', '#lookup-panel', '#online', '#artwork-panel']) document.querySelector(selector).hidden = false;
    document.querySelector('#recovery-status').textContent = '';
    existingTags = data.tags;
    existingValues = data.tag_values || {};
    fileVersion = data.version;
    writeEnabled = data.write_enabled;
    if (!writeEnabled) document.querySelector('#save-status').textContent = '現在は読み取り専用です。';
    comments = data.comments || [];
    fileFormat = data.format;
    existingArtworks = data.artworks || {};
    onlineImages = {front: null, back: null};
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
    document.querySelector('#existing-artwork-label').textContent = data.format === 'mp3' ? '既存画像（表）' : '既存画像';
    document.querySelector('#existing-back-container').hidden = data.format !== 'mp3';
    const back = document.querySelector('#existing-back-artwork');
    back.hidden = !data.back_artwork;
    document.querySelector('#no-existing-back-artwork').hidden = data.back_artwork;
    back.src = data.back_artwork ? `/api/files/${encodeURIComponent(id)}/artwork?side=back` : '';
    setDraft(existingTags);
    renderComparison();
    renderArtworkControls();
    loadHistory();
    selected.hidden = false;
    status.hidden = true;
  } catch (error) {
    if (current !== generation) return;
    status.textContent = error.message;
    if (error.status !== 422) return;
    try {
      const data = await getJson(`/api/files/${encodeURIComponent(id)}/recovery`);
      if (current !== generation) return;
      currentFile = id; fileVersion = data.version; fileFormat = data.format; writeEnabled = data.write_enabled;
      existingTags = {}; existingValues = {}; existingArtworks = {}; comments = [];
      setDraft(existingTags);
      for (const selector of ['#player-panel', '#lookup-panel', '#online', '#artwork-panel']) document.querySelector(selector).hidden = true;
      selected.hidden = false;
      status.textContent = 'タグを読み取れません。未完了の処理がある場合は履歴から復元できます。';
      document.querySelector('#recovery-status').textContent = status.textContent;
      await loadHistory();
    } catch (recoveryError) { if (current === generation) status.textContent = recoveryError.message; }
  }
}

async function chooseRelease(recording, release, current) {
  clearOnlineImage('front');
  clearOnlineBack();
  operationBusy = true;
  renderComparison();
  renderArtworkControls();
  const lookupStatus = document.querySelector('#lookup-status');
  lookupStatus.textContent = 'リリース情報を取得中…';
  try {
    const data = await getJson(`/api/recordings/${encodeURIComponent(recording.id)}/releases/${encodeURIComponent(release.id)}`);
    if (current !== lookupGeneration) return;
    setDraft(data);
    renderComparison();
    renderArtworkControls();
    loadOnlineImage('front', release.id, current);
    loadOnlineImage('back', release.id, current);
    lookupStatus.textContent = `表示中の候補: ${[release.title, release.date, release.country].filter(Boolean).join(' · ')}（別の候補も選べます）`;
  } catch (error) { if (current === lookupGeneration) lookupStatus.textContent = error.message; }
  finally { operationBusy = false; renderComparison(); renderArtworkControls(); }
}

function normalizeAlbum(name) {
  return name.trim().normalize('NFKC').toLocaleLowerCase().replace(/\s+/g, ' ');
}

function hasPendingEdits() {
  if (typeof draftTags === 'undefined') return false;
  return selectedFields.size > 0 || selectedComment !== baselineComment || selectedArtwork.size > 0 ||
    Object.keys(artworkChoices).some((side) => artworkChoices[side] !== artworkBaseline[side] ||
      artworkTargets[side] !== artworkTargetBaseline[side] || Boolean(artworkUploads[side])) ||
    Object.keys(labels).some((key) => draftTags[key] !== baselineTags[key]);
}

function confirmDiscardEdits() {
  if (typeof operationBusy !== 'undefined' && operationBusy) return false;
  if (typeof pendingArtworkLoads !== 'undefined' && pendingArtworkLoads > 0) return false;
  return !hasPendingEdits() || window.confirm('保存前の入力・選択を破棄しますか？');
}

function resetComparisonDraft() {
  if (typeof setDraft === 'function') setDraft(existingTags);
  renderComparison();
  if (typeof renderArtworkControls === 'function') renderArtworkControls();
}

function clearOnlineBack() {
  clearOnlineImage('back');
}

function clearOnlineImage(side) {
  const image = document.querySelector(side === 'front' ? '#online-artwork' : '#online-back-artwork');
  image.onload = null;
  image.onerror = null;
  image.hidden = true;
  image.src = '';
  document.querySelector(side === 'front' ? '#no-online-artwork' : '#no-online-back-artwork').hidden = false;
  if (typeof onlineImages !== 'undefined') onlineImages[side] = null;
  if (typeof onlineUrls !== 'undefined' && onlineUrls[side]) {
    URL.revokeObjectURL(onlineUrls[side]);
    onlineUrls[side] = null;
  }
}

async function loadOnlineImage(side, releaseId, current) {
  try {
    const response = await fetch(`/api/releases/${encodeURIComponent(releaseId)}/artwork/${side}`);
    if (!response.ok) return;
    const blob = await response.blob();
    const data = await artworkData(blob);
    if (current !== lookupGeneration) return;
    onlineImages[side] = data;
    onlineUrls[side] = URL.createObjectURL(blob);
    const image = document.querySelector(side === 'front' ? '#online-artwork' : '#online-back-artwork');
    image.src = onlineUrls[side];
    image.hidden = false;
    document.querySelector(side === 'front' ? '#no-online-artwork' : '#no-online-back-artwork').hidden = true;
    renderArtworkControls();
    updateEditActions();
  } catch (error) {
    if (current === lookupGeneration) console.warn('アートワークを読み込めませんでした。', error);
  }
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
      if (!confirmDiscardEdits()) return;
      lookupGeneration++;
      clearOnlineImage('front');
      clearOnlineBack();
      for (const item of rows) item.button.setAttribute('aria-pressed', 'false');
      primary.setAttribute('aria-pressed', 'true');
      editionList.replaceChildren();
      editionHint.textContent = `${group.title} の版を選択してください。`;
      document.querySelector('#lookup-status').textContent = '版を選ぶとタグ情報を比較できます。';
      document.querySelector('#online-artwork').hidden = true;
      document.querySelector('#no-online-artwork').hidden = false;
      resetComparisonDraft();
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
          if (!confirmDiscardEdits()) return;
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
  selectedLanguage = select.value;
}

function changeLanguage() {
  if (lastRecordings === null) return;
  if (!confirmDiscardEdits()) {
    document.querySelector('#release-language').value = selectedLanguage;
    return;
  }
  selectedLanguage = document.querySelector('#release-language').value;
  clearOnlineImage('front');
  clearOnlineBack();
  const current = ++lookupGeneration;
  document.querySelector('#online-artwork').hidden = true;
  document.querySelector('#no-online-artwork').hidden = false;
  setDraft(existingTags);
  renderComparison();
  showCandidates(lastRecordings, current, document.querySelector('#release-language').value);
}

async function startLookup() {
  if (!currentFile) return;
  if (!confirmDiscardEdits()) return;
  clearOnlineImage('front');
  clearOnlineBack();
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
  setDraft(existingTags);
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
document.querySelector('#select-all').addEventListener('click', () => {
  for (const key of Object.keys(labels)) if (draftTags[key]) selectedFields.add(key);
  for (const side of Object.keys(artworkChoices)) {
    const candidate = artworkCandidate(side);
    if (candidate && !candidate.delete && artworkChanged(side)) selectedArtwork.add(side);
  }
  renderComparison();
  renderArtworkControls();
});
document.querySelector('#clear-all').addEventListener('click', () => { selectedFields.clear(); selectedArtwork.clear(); renderComparison(); renderArtworkControls(); });
document.querySelector('#reset-tags').addEventListener('click', () => {
  draftTags = {...baselineTags};
  selectedFields.clear();
  selectedComment = baselineComment;
  resetArtworkState();
  renderComparison();
  renderArtworkControls();
});
document.querySelector('#save-tags').addEventListener('click', saveTags);
document.querySelector('#refresh-existing').addEventListener('click', async () => {
  try {
    await refreshExisting();
    document.querySelector('#refresh-existing').hidden = true;
    document.querySelector('#save-status').textContent = '既存タグを再読み込みしました。変更内容を確認してください。';
  } catch (error) { document.querySelector('#save-status').textContent = error.message; }
});
window.addEventListener('beforeunload', (event) => {
  if (operationBusy || pendingArtworkLoads || hasPendingEdits()) event.preventDefault();
});
showDirectory();
loadPendingRecoveries();
