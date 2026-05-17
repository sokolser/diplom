import { useMemo, useState } from 'react';
import { analyzeDocuments, generateChangeNoticePdf } from './api.js';
import { useLocalStorage } from './storage.js';
import {
  createEmptyBlock,
  createNotice,
  makePdfPayload,
  normalizeAnalyzeResponse,
  resolveChangeNumberFromAnalyzeResponse,
  sortBlocks,
  formatBlockText,
  parseBlockText,
  isEmptyBlock,
} from './utils.js';

const STORAGE_KEY = 'change-notice-frontend-state-v13';



function Field({ label, value, onChange, placeholder = '', type = 'text' }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type={type} value={value || ''} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
    </label>
  );
}

function TextAreaField({ label, value, onChange, minRows = 5 }) {
  return (
    <label className="field fieldTextArea">
      <span>{label}</span>
      <textarea rows={minRows} value={value || ''} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function StatusBar({ status, error }) {
  return (
    <div className={`statusBar ${error ? 'statusError' : ''}`}>
      <span>{error || status || 'Готово'}</span>
    </div>
  );
}

function LeftPanel({ notices, activeNoticeId, activeBlockId, onSelectNotice, onSelectBlock, onCreateNotice, onCreateBlock, onDeleteBlock, onDeleteNotice, onDeleteAllBlocks }) {
  const [search, setSearch] = useState('');

  const filteredNotices = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return notices;
    return notices.filter((notice) => {
      const noticeHit = notice.notice_id.toLowerCase().includes(q);
      const blockHit = notice.blocks.some((block) => `${block.decimal_number} ${block.doc_type}`.toLowerCase().includes(q));
      return noticeHit || blockHit;
    });
  }, [notices, search]);

  return (
    <aside className="panel leftPanel">
      <div className="panelHeader compactHeader">
        <div>
          <h2>Извещения</h2>
          <p>Дерево документов</p>
        </div>
        <button className="iconButton" onClick={onCreateNotice} title="Создать извещение">+</button>
      </div>

      <div className="searchBox">
        <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Поиск" />
      </div>

      <div className="treeArea">
        {filteredNotices.map((notice) => (
          <div key={notice.id} className="treeNotice">
            <div className="treeNoticeHeader">
              <button
                className={`treeNoticeButton ${notice.id === activeNoticeId ? 'activeNotice' : ''}`}
                onClick={() => onSelectNotice(notice.id)}
              >
                <span className="treeIcon">▾</span>
                <span className="treeTitle">{notice.notice_id}</span>
              </button>
              <button className="miniDangerButton" onClick={() => onDeleteNotice(notice.id)} title="Удалить извещение">×</button>
            </div>

            <div className="treeBlocks">
              {sortBlocks(notice.blocks).map((block) => (
                <div key={block.id} className="treeBlockRow">
                  <button
                    className={`treeBlockButton ${block.id === activeBlockId ? 'activeBlock' : ''}`}
                    onClick={() => onSelectBlock(notice.id, block.id)}
                  >
                    <span className="treeIcon">▦</span>
                    <span className="treeTitle">{block.decimal_number || 'Новый блок'}</span>
                  </button>
                  <button className="miniDangerButton" onClick={() => onDeleteBlock(notice.id, block.id)} title="Удалить блок">×</button>
                </div>
              ))}
            </div>

            <button className="addBlockButton" onClick={() => onCreateBlock(notice.id)}>+ Добавить блок</button>
            {notice.blocks.length > 0 && (
              <button className="deleteAllBlocksButton" onClick={() => onDeleteAllBlocks(notice.id)}>Удалить все блоки</button>
            )}
          </div>
        ))}
      </div>
    </aside>
  );
}

function UploadAndAnalyze({ onApplyBlocks, setStatus, setError }) {
  const [fileV1, setFileV1] = useState(null);
  const [fileV2, setFileV2] = useState(null);
  const [busy, setBusy] = useState(false);

  async function handleAnalyze() {
    setBusy(true);
    setError('');
    setStatus('Анализ документов...');
    try {
      const response = await analyzeDocuments({ fileV1, fileV2 });
      const blocks = normalizeAnalyzeResponse(response);
      const detectedChangeNumber = resolveChangeNumberFromAnalyzeResponse(response);
      if (!blocks.length) {
        throw new Error('Backend вернул ответ без блоков изменения. Проверь поле result.block.');
      }
      onApplyBlocks(blocks, detectedChangeNumber);
      setStatus(`Анализ завершен. Получено блоков: ${blocks.length}`);
    } catch (error) {
      setError(error.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="uploadBox">
      <div className="uploadGrid">
        <label className="fileField">
          <span>Старая версия PDF</span>
          <input type="file" accept="application/pdf" onChange={(event) => setFileV1(event.target.files?.[0] || null)} />
        </label>
        <label className="fileField">
          <span>Новая версия PDF</span>
          <input type="file" accept="application/pdf" onChange={(event) => setFileV2(event.target.files?.[0] || null)} />
        </label>
      </div>
      <button className="primaryButton" onClick={handleAnalyze} disabled={busy || !fileV1 || !fileV2}>
        {busy ? 'Анализ...' : 'Загрузить и проанализировать'}
      </button>
    </section>
  );
}

function CenterPanel({ activeBlock, onUpdateBlock, onApplyBlocks, setStatus, setError }) {
  if (!activeBlock) {
    return (
      <main className="panel centerPanel emptyState">
        <h2>Нет выбранного блока</h2>
        <p>Создай извещение и добавь блок изменения.</p>
      </main>
    );
  }

  const blockText = activeBlock.block_text ?? formatBlockText(activeBlock);

  function updateBlockText(value) {
    const parsed = parseBlockText(value, activeBlock);
    onUpdateBlock({
      ...activeBlock,
      ...parsed,
      block_text: value,
    });
  }

  return (
    <main className="panel centerPanel">
      <div className="panelHeader">
        <div>
          <h1>Блок содержания изменения</h1>
          <p>Результат анализа можно править перед формированием PDF</p>
        </div>
      </div>

      <UploadAndAnalyze onApplyBlocks={onApplyBlocks} setStatus={setStatus} setError={setError} />

      <section className="blockForm blockTextForm">
        <div className="notesHeader singleNotesHeader">
          <div>
            <h3>Текст блока изменения</h3>
            <p>Редактируй финальный текст блока: обозначение, действие, строку «Примечания:», нумерацию и журнал.</p>
          </div>
        </div>

        <TextAreaField
          label=""
          value={blockText}
          onChange={updateBlockText}
          minRows={22}
        />
      </section>
    </main>
  );
}

function RightPanel({ notice, onUpdateNotice, onGeneratePdf }) {
  return (
    <aside className="panel rightPanel">
      <div className="panelHeader compactHeader">
        <div>
          <h2>Реквизиты извещения</h2>
          <p>Эти поля попадут в шапку PDF</p>
        </div>
      </div>

      <div className="rightScroll">
        <Field label="Организация" value={notice.organization} onChange={(value) => onUpdateNotice({ organization: value })} />
        <Field label="Обозначение извещения" value={notice.notice_id} onChange={(value) => onUpdateNotice({ notice_id: value })} />
        <Field label="Дата" value={notice.notice_date} onChange={(value) => onUpdateNotice({ notice_date: value })} />
        <Field label="Причина изменения" value={notice.change_reason} onChange={(value) => onUpdateNotice({ change_reason: value })} />

        <div className="formGrid twoColumns">
          <Field label="Отдел" value={notice.department} onChange={(value) => onUpdateNotice({ department: value })} placeholder="например: отдел кабельной сети" />
          <Field label="Код" value={notice.change_code} onChange={(value) => onUpdateNotice({ change_code: value })} />
        </div>

        <Field label="Указание о внедрении" value={notice.implementation_instruction} onChange={(value) => onUpdateNotice({ implementation_instruction: value })} />
        <Field label="Разослать" value={notice.mailing_list} onChange={(value) => onUpdateNotice({ mailing_list: value })} />
        <Field label="Применяемость" value={notice.applicability} onChange={(value) => onUpdateNotice({ applicability: value })} />

        <div className="formGrid twoColumns">
          <Field label="Разработал" value={notice.developer} onChange={(value) => onUpdateNotice({ developer: value })} />
          <Field label="Проверил" value={notice.checker} onChange={(value) => onUpdateNotice({ checker: value })} />
        </div>

        <div className="formGrid twoColumns">
          <Field label="Тех. контроль" value={notice.technical_control} onChange={(value) => onUpdateNotice({ technical_control: value })} />
          <Field label="Нормоконтроль" value={notice.norm_control} onChange={(value) => onUpdateNotice({ norm_control: value })} />
        </div>

        <div className="formGrid twoColumns">
          <Field label="Утвердил" value={notice.approver} onChange={(value) => onUpdateNotice({ approver: value })} />
          <Field label="Пред. заказ." value={notice.customer_representative} onChange={(value) => onUpdateNotice({ customer_representative: value })} />
        </div>

        <div className="hintBox">
          Заполни реквизиты, проверь содержание изменения в центральной зоне и сформируй PDF.
        </div>
      </div>

      <div className="rightActions">
        <button className="primaryButton" onClick={onGeneratePdf}>Сформировать PDF</button>
      </div>
    </aside>
  );
}

export default function App() {
  const [notices, setNotices] = useLocalStorage(STORAGE_KEY, [createNotice(1)]);
  const [activeNoticeId, setActiveNoticeId] = useLocalStorage(`${STORAGE_KEY}-active-notice`, notices[0]?.id || '');
  const [activeBlockId, setActiveBlockId] = useLocalStorage(`${STORAGE_KEY}-active-block`, notices[0]?.blocks?.[0]?.id || '');
  const [status, setStatus] = useState('Готово');
  const [error, setError] = useState('');

  const activeNotice = useMemo(() => notices.find((notice) => notice.id === activeNoticeId) || notices[0], [notices, activeNoticeId]);
  const activeBlock = useMemo(() => activeNotice?.blocks.find((block) => block.id === activeBlockId) || activeNotice?.blocks?.[0], [activeNotice, activeBlockId]);
  const pdfPayload = useMemo(() => (activeNotice ? makePdfPayload(activeNotice) : {}), [activeNotice]);

  function updateNoticeInList(noticeId, updater) {
    setNotices((prev) => prev.map((notice) => (notice.id === noticeId ? updater(notice) : notice)));
  }

  function handleCreateNotice() {
    const next = createNotice(notices.length + 1);
    setNotices((prev) => [...prev, next]);
    setActiveNoticeId(next.id);
    setActiveBlockId(next.blocks[0].id);
  }

  function handleDeleteNotice(noticeId) {
    const notice = notices.find((item) => item.id === noticeId);
    const label = notice?.notice_id || 'выбранное извещение';
    if (!window.confirm(`Удалить извещение ${label}?`)) return;

    const remaining = notices.filter((item) => item.id !== noticeId);
    if (!remaining.length) {
      const next = createNotice(1);
      setNotices([next]);
      setActiveNoticeId(next.id);
      setActiveBlockId(next.blocks[0].id);
      setStatus(`Извещение ${label} удалено. Создано новое пустое извещение.`);
      return;
    }

    setNotices(remaining);
    if (noticeId === activeNoticeId) {
      setActiveNoticeId(remaining[0].id);
      setActiveBlockId(remaining[0].blocks[0]?.id || '');
    }
    setStatus(`Извещение ${label} удалено.`);
  }

  function handleCreateBlock(noticeId = activeNotice.id) {
    const block = createEmptyBlock();
    updateNoticeInList(noticeId, (notice) => ({ ...notice, blocks: [...notice.blocks, block] }));
    setActiveNoticeId(noticeId);
    setActiveBlockId(block.id);
  }

  function handleDeleteBlock(noticeId, blockId) {
    const notice = notices.find((item) => item.id === noticeId);
    if (!notice) return;
    const blocks = notice.blocks.filter((block) => block.id !== blockId);
    const nextBlocks = blocks;
    updateNoticeInList(noticeId, (item) => ({ ...item, blocks: nextBlocks }));
    if (blockId === activeBlockId) setActiveBlockId(nextBlocks[0]?.id || '');
  }

  function handleDeleteAllBlocks(noticeId) {
    const notice = notices.find((item) => item.id === noticeId);
    if (!notice || !notice.blocks.length) return;
    if (!window.confirm(`Удалить все блоки из извещения ${notice.notice_id}?`)) return;
    updateNoticeInList(noticeId, (item) => ({ ...item, blocks: [] }));
    if (noticeId === activeNoticeId) setActiveBlockId('');
    setStatus(`Все блоки извещения ${notice.notice_id} удалены.`);
  }

  function handleSelectBlock(noticeId, blockId) {
    setActiveNoticeId(noticeId);
    setActiveBlockId(blockId);
  }

  function handleUpdateNotice(patch) {
    updateNoticeInList(activeNotice.id, (notice) => ({ ...notice, ...patch }));
  }

  function handleUpdateBlock(updatedBlock) {
    updateNoticeInList(activeNotice.id, (notice) => ({
      ...notice,
      blocks: notice.blocks.map((block) => (block.id === updatedBlock.id ? updatedBlock : block)),
    }));
  }

  function handleApplyBlocks(newBlocks, detectedChangeNumber = '1') {
    const autoChangeNumber = String(detectedChangeNumber || activeNotice.change_number || '1').trim() || '1';
    const currentBlocks = [...(activeNotice.blocks || [])].filter((block) => !isEmptyBlock(block));
    const preparedBlocks = newBlocks.map((rawBlock) => {
      const sameBlock = currentBlocks.find((block) => block.decimal_number === rawBlock.decimal_number && block.doc_type === rawBlock.doc_type);
      return {
        ...rawBlock,
        id: sameBlock?.id || rawBlock.id,
        change_number: rawBlock.change_number || autoChangeNumber,
      };
    });

    // Активным должен становиться именно блок из последнего анализа.
    // Сортировка нужна только для порядка отображения/печати, но она не должна
    // перехватывать фокус на первый блок по децимальному номеру.
    let nextActiveBlockId = preparedBlocks[0]?.id || '';

    updateNoticeInList(activeNotice.id, (notice) => {
      let nextBlocks = [...(notice.blocks || [])].filter((block) => !isEmptyBlock(block));

      for (const newBlock of preparedBlocks) {
        const sameIndex = nextBlocks.findIndex((block) => block.id === newBlock.id || (block.decimal_number === newBlock.decimal_number && block.doc_type === newBlock.doc_type));
        if (sameIndex >= 0) {
          const keptId = nextBlocks[sameIndex].id;
          nextBlocks[sameIndex] = { ...nextBlocks[sameIndex], ...newBlock, id: keptId };

          if (newBlock.id === nextActiveBlockId) {
            nextActiveBlockId = keptId;
          }
        } else {
          nextBlocks.push(newBlock);
        }
      }

      return { ...notice, change_number: autoChangeNumber, blocks: sortBlocks(nextBlocks) };
    });

    setActiveNoticeId(activeNotice.id);
    if (nextActiveBlockId) setActiveBlockId(nextActiveBlockId);
  }

  async function handleGeneratePdf() {
    setError('');
    setStatus('Формирование PDF...');
    try {
      const result = await generateChangeNoticePdf(pdfPayload);
      setStatus(`PDF сформирован${result?.filename ? `: ${result.filename}` : ''}`);
    } catch (error) {
      setError(error.message);
    }
  }

  if (!activeNotice) {
    return (
      <div className="bootScreen">
        <button className="primaryButton" onClick={handleCreateNotice}>Создать первое извещение</button>
      </div>
    );
  }

  return (
    <div className="appShell">
      <header className="topBar">
        <div className="brand">
          <span className="brandMark">ИИ</span>
          <div>
            <strong>Виртуальный ассистент КД</strong>
            <span>Формирование извещений об изменении</span>
          </div>
        </div>
        <div className="topActions">
          <button className="secondaryButton" onClick={() => localStorage.clear()}>Очистить localStorage</button>
          <button className="primaryButton" onClick={handleGeneratePdf}>PDF</button>
        </div>
      </header>

      <div className="workspace">
        <LeftPanel
          notices={notices}
          activeNoticeId={activeNotice.id}
          activeBlockId={activeBlock?.id}
          onSelectNotice={setActiveNoticeId}
          onSelectBlock={handleSelectBlock}
          onCreateNotice={handleCreateNotice}
          onCreateBlock={handleCreateBlock}
          onDeleteBlock={handleDeleteBlock}
          onDeleteNotice={handleDeleteNotice}
          onDeleteAllBlocks={handleDeleteAllBlocks}
        />

        <CenterPanel
          activeBlock={activeBlock}
          onUpdateBlock={handleUpdateBlock}
          onApplyBlocks={handleApplyBlocks}
          setStatus={setStatus}
          setError={setError}
        />

        <RightPanel
          notice={activeNotice}
          onUpdateNotice={handleUpdateNotice}
          onGeneratePdf={handleGeneratePdf}
        />
      </div>

      <StatusBar status={status} error={error} />
    </div>
  );
}

