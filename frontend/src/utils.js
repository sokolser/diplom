export function makeId(prefix = 'id') {
  return `${prefix}-${Math.random().toString(16).slice(2)}-${Date.now().toString(16)}`;
}

export function inferDocType(decimalNumber = '', fallback = 'Сборочный чертеж') {
  const value = String(decimalNumber).toUpperCase();
  if (value.includes('ПЭ4')) return 'Перечень элементов';
  if (value.includes(' Э4')) return 'Схема электрическая соединений';
  if (value.includes(' СБ')) return 'Сборочный чертеж';
  if (value.includes(' СП')) return 'Спецификация';
  // У спецификаций часто нет буквенного кода в обозначении.
  return fallback || 'Спецификация';
}

export function makeJournalEntry(journalNumber = 'XX', journalEntryNumber = 'XX') {
  const journal = String(journalNumber || 'XX').trim() || 'XX';
  const entry = String(journalEntryNumber || 'XX').trim() || 'XX';
  return `Журнал № ${journal}, запись № ${entry}.`;
}

export function createEmptyBlock() {
  return {
    id: makeId('block'),
    doc_type: 'Сборочный чертеж',
    decimal_number: '',
    action: '',
    notes: [],
    journal_entry: '',
    block_text: '',
  };
}

export function isEmptyBlock(block) {
  const rawText = normalizeText(block?.block_text || '').trim();
  const decimalNumber = String(block?.decimal_number || '').trim();
  const action = String(block?.action || '').trim();
  const notes = Array.isArray(block?.notes) ? block.notes : [];
  const hasNotes = notes.some((note) => String(note || '').trim());
  return !rawText && !decimalNumber && !action && !hasNotes;
}

export function createNotice(index = 1) {
  return {
    id: makeId('notice'),
    notice_id: `ИИ-${String(index).padStart(3, '0')}`,
    notice_date: new Date().toLocaleDateString('ru-RU'),
    organization: 'АО "ЭЙРБУРГ"',
    change_reason: 'Уточнение конструкторской документации',
    department: 'отдел кабельной сети',
    change_code: '01',
    // Номер изменения не вводится пользователем. Значение обновляется автоматически после анализа PDF.
    change_number: '1',
    implementation_instruction: '-',
    mailing_list: 'АО УЗГА, ОТС, ПДО, ПРБ',
    developer: '',
    checker: '',
    technical_control: '',
    norm_control: '',
    approver: '',
    customer_representative: 'Согл. не подл.',
    applicability: '',
    journal_number: 'XX',
    journal_entry_number: 'XX',
    blocks: [createEmptyBlock()],
  };
}

export function normalizeText(value) {
  return String(value || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
}


function normalizeChangeNumberValue(value) {
  const text = String(value ?? '').trim();
  if (!text || /^[-—]+$/.test(text)) return '';
  const match = text.match(/\d+/);
  return match ? String(Number(match[0])) : '';
}

function nextChangeNumber(value) {
  const normalized = normalizeChangeNumberValue(value);
  if (!normalized) return '1';
  return String(Number(normalized) + 1);
}

function pickFirstValue(paths, source) {
  for (const path of paths) {
    let current = source;
    for (const key of path) {
      if (current == null) break;
      current = current[key];
    }
    const normalized = normalizeChangeNumberValue(current);
    if (normalized) return normalized;
  }
  return '';
}

export function resolveChangeNumberFromAnalyzeResponse(response) {
  const root = response || {};
  const result = root.result || {};
  const facts = root.facts || {};

  // Если backend уже извлек номер изменения из новой версии документа, берем его напрямую.
  const newVersionNumber = pickFirstValue([
    ['result', 'block', 0, 'change_number'],
    ['result', 'blocks', 0, 'change_number'],
    ['block', 0, 'change_number'],
    ['blocks', 0, 'change_number'],
    ['change_number'],
    ['detected_change_number'],
    ['metadata_v2', 'change_number'],
    ['metadata_v2', 'revision_number'],
    ['result', 'change_number'],
    ['result', 'detected_change_number'],
    ['result', 'metadata_v2', 'change_number'],
    ['result', 'metadata_v2', 'revision_number'],
    ['facts', 'change_number'],
    ['facts', 'metadata_v2', 'change_number'],
    ['facts', 'metadata_v2', 'revision_number'],
  ], root);
  if (newVersionNumber) return newVersionNumber;

  // Если есть только номер в старой версии, номер изменения для извещения = старый + 1.
  const oldVersionNumber = pickFirstValue([
    ['metadata_v1', 'change_number'],
    ['metadata_v1', 'revision_number'],
    ['result', 'metadata_v1', 'change_number'],
    ['result', 'metadata_v1', 'revision_number'],
    ['facts', 'metadata_v1', 'change_number'],
    ['facts', 'metadata_v1', 'revision_number'],
    ['old_change_number'],
    ['result', 'old_change_number'],
    ['facts', 'old_change_number'],
  ], root);

  return nextChangeNumber(oldVersionNumber);
}

export function cleanNote(value) {
  return normalizeText(value)
    .split('\n')
    .map((line) => line.trimEnd())
    .join('\n')
    .trim();
}

export function getJournalLineFromBlock(block) {
  return String(block?.journal_entry || makeJournalEntry()).trim();
}

export function formatNotesWithNumbers(notes) {
  const cleanNotes = (Array.isArray(notes) ? notes : [])
    .map(cleanNote)
    .filter(Boolean);

  if (!cleanNotes.length) return '1 ';

  return cleanNotes
    .map((note, index) => {
      const lines = note.split('\n');
      const first = lines[0] || '';
      const rest = lines.slice(1);
      return [`${index + 1} ${first}`, ...rest].join('\n');
    })
    .join('\n');
}

export function formatBlockText(block) {
  const decimalNumber = String(block?.decimal_number || '').trim();
  const action = String(block?.action || 'Заменить (Лист 1 заменить)').trim();
  const notes = formatNotesWithNumbers(block?.notes || []);
  const journalEntry = getJournalLineFromBlock(block);

  return [
    decimalNumber,
    action,
    'Примечания:',
    notes,
    journalEntry,
  ]
    .filter((part) => String(part || '').trim())
    .join('\n');
}

function stripNoteNumber(line) {
  return String(line || '').replace(/^\s*\d+\s*[.)]?\s+/, '').trimEnd();
}

function isNumberedNoteLine(line) {
  return /^\s*\d+\s*[.)]?\s+\S/.test(String(line || ''));
}

function isJournalLine(line) {
  return /^\s*Журнал\s*№/i.test(String(line || ''));
}

function isNotesHeader(line) {
  return /^\s*Примечани[ея]\s*:?\s*$/i.test(String(line || ''));
}

export function parseNumberedNotes(text) {
  const rawLines = normalizeText(text).split('\n');
  const notes = [];
  let current = [];

  for (const rawLine of rawLines) {
    const line = rawLine.trimEnd();
    if (!line.trim()) continue;
    if (isJournalLine(line)) break;
    if (isNotesHeader(line)) continue;

    if (isNumberedNoteLine(line)) {
      if (current.length) notes.push(current.join('\n').trim());
      current = [stripNoteNumber(line)];
      continue;
    }

    // Если пользователь удалил номера, считаем новую строку без дефиса новым примечанием.
    if (current.length && !line.trimStart().startsWith('-')) {
      notes.push(current.join('\n').trim());
      current = [line.trimStart()];
      continue;
    }

    current.push(line.trimStart());
  }

  if (current.length) notes.push(current.join('\n').trim());
  return notes.map(cleanNote).filter(Boolean);
}

export function parseJournalEntry(text, fallback = makeJournalEntry()) {
  const line = normalizeText(text).split('\n').find(isJournalLine);
  return line ? line.trim() : fallback;
}

export function parseBlockText(value, fallbackBlock = {}) {
  const raw = normalizeText(value);
  const lines = raw.split('\n');

  const firstContentIndex = lines.findIndex((line) => line.trim());
  if (firstContentIndex === -1) {
    return {
      decimal_number: fallbackBlock.decimal_number || '',
      action: fallbackBlock.action || 'Заменить (Лист 1 заменить)',
      doc_type: fallbackBlock.doc_type || 'Сборочный чертеж',
      notes: [''],
      journal_entry: fallbackBlock.journal_entry || makeJournalEntry(),
      block_text: '',
    };
  }

  const decimalNumber = lines[firstContentIndex]?.trim() || fallbackBlock.decimal_number || '';
  const actionLine = lines[firstContentIndex + 1]?.trim() || fallbackBlock.action || 'Заменить (Лист 1 заменить)';

  const afterActionLines = lines.slice(firstContentIndex + 2);
  const notesHeaderIndex = afterActionLines.findIndex(isNotesHeader);
  const notesSource = notesHeaderIndex >= 0
    ? afterActionLines.slice(notesHeaderIndex + 1).join('\n')
    : afterActionLines.join('\n');

  const notes = parseNumberedNotes(notesSource);
  const journalEntry = parseJournalEntry(raw, fallbackBlock.journal_entry || makeJournalEntry());

  return {
    decimal_number: decimalNumber,
    action: actionLine,
    doc_type: inferDocType(decimalNumber, fallbackBlock.doc_type),
    notes: notes.length ? notes : [''],
    journal_entry: journalEntry,
    block_text: raw,
  };
}

export function normalizeBlock(block, formattedText = null) {
  const notes = Array.isArray(block.notes) ? block.notes : block.notes ? [String(block.notes)] : [''];
  const normalized = {
    id: block.id || makeId('block'),
    doc_type: block.doc_type || block.document_type || inferDocType(block.decimal_number, 'Документ'),
    decimal_number: block.decimal_number || '',
    action: block.action || 'Заменить (Лист 1 заменить)',
    notes,
    journal_entry: block.journal_entry || makeJournalEntry(),
    change_number: block.change_number || block.changeNumber || block.revision_number || block.revisionNumber || undefined,
  };

  return {
    ...normalized,
    block_text: block.block_text || block.notes_text || formattedText || formatBlockText(normalized),
  };
}

export function normalizeAnalyzeResponse(response) {
  const result = response?.result || response;
  const blocks = result?.block || result?.blocks || response?.block || response?.blocks || [];
  if (!Array.isArray(blocks)) return [];

  // Если backend вернул один готовый formatted_text, показываем в редакторе именно его,
  // а не техническую структуру notes[].
  if (blocks.length === 1 && result?.formatted_text) {
    return [normalizeBlock(blocks[0], result.formatted_text)];
  }

  return blocks.map((block) => normalizeBlock(block));
}

export function stripUiFieldsFromBlock(block) {
  const parsed = parseBlockText(block.block_text ?? formatBlockText(block), block);
  return {
    doc_type: parsed.doc_type,
    decimal_number: parsed.decimal_number,
    action: parsed.action,
    notes: parsed.notes.filter((note) => String(note).trim()),
    journal_entry: parsed.journal_entry || block.journal_entry || makeJournalEntry(),
    change_number: block.change_number || undefined,
  };
}

export function makePdfPayload(notice) {
  const cleanBlocks = sortBlocks(notice.blocks || [])
    .filter((block) => !isEmptyBlock(block))
    .map(stripUiFieldsFromBlock)
    .filter((block) => String(block.decimal_number || block.action || '').trim() || block.notes.some((note) => String(note || '').trim()));

  return {
    notice_id: notice.notice_id,
    notice_date: notice.notice_date,
    organization: notice.organization,
    designation: notice.designation || 'См. ниже',
    change_reason: notice.change_reason,
    department: notice.department,
    change_code: notice.change_code,
    code: notice.change_code,
    change_number: notice.change_number,
    implementation_instruction: notice.implementation_instruction,
    mailing_list: notice.mailing_list,
    send_to: notice.mailing_list,
    attachment: notice.attachment || '',
    developer: notice.developer,
    checker: notice.checker,
    technical_control: notice.technical_control || notice.tech_control || '',
    tech_control: notice.technical_control || notice.tech_control || '',
    norm_control: notice.norm_control,
    applicability: notice.applicability || '', 
    approver: notice.approver,
    customer_representative: notice.customer_representative,
    journal_number: notice.journal_number,
    journal_entry_number: notice.journal_entry_number,
    block: cleanBlocks,
  };
}

const docCodeOrder = {
  'СП': 1,
  'ПЭ4': 2,
  'СБ': 3,
  'Э4': 4,
};

function normalizeDecimalNumber(value = '') {
  return String(value || '')
    .toUpperCase()
    .replace(/\s+/g, ' ')
    .trim();
}

function getBaseDecimalNumber(value = '') {
  return normalizeDecimalNumber(value)
    .replace(/\s+(СП|ПЭ4|СБ|Э4)$/iu, '')
    .trim();
}

function getDocumentCode(block = {}) {
  const decimalNumber = normalizeDecimalNumber(block.decimal_number);
  const codeFromDecimal = decimalNumber.match(/\s(СП|ПЭ4|СБ|Э4)$/iu)?.[1];
  if (codeFromDecimal) return codeFromDecimal.toUpperCase();

  const docType = String(block.doc_type || '').toLowerCase();
  if (docType.includes('спецификация')) return 'СП';
  if (docType.includes('перечень')) return 'ПЭ4';
  if (docType.includes('сборочный')) return 'СБ';
  if (docType.includes('схема')) return 'Э4';

  return '';
}

export function sortBlocks(blocks) {
  return [...(blocks || [])].sort((a, b) => {
    const baseA = getBaseDecimalNumber(a?.decimal_number);
    const baseB = getBaseDecimalNumber(b?.decimal_number);

    const decimalCompare = baseA.localeCompare(baseB, 'ru', {
      numeric: true,
      sensitivity: 'base',
    });

    if (decimalCompare !== 0) return decimalCompare;

    const orderA = docCodeOrder[getDocumentCode(a)] ?? 999;
    const orderB = docCodeOrder[getDocumentCode(b)] ?? 999;
    if (orderA !== orderB) return orderA - orderB;

    return normalizeDecimalNumber(a?.decimal_number).localeCompare(
      normalizeDecimalNumber(b?.decimal_number),
      'ru',
      { numeric: true, sensitivity: 'base' },
    );
  });
}
