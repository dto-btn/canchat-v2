export type CsvUserRow = {
	rowNumber: number;
	name: string;
	email: string;
	password: string;
	role: string;
};

export type CsvUserImportError = {
	rowNumber: number;
	message: string;
};

export type CsvUserImportResult = {
	rows: CsvUserRow[];
	errors: CsvUserImportError[];
};

export const countInvalidCsvRows = (errors: CsvUserImportError[]): number => {
	return new Set(errors.map((error) => error.rowNumber)).size;
};

const VALID_ROLES = new Set(['admin', 'user', 'pending', 'analyst', 'global_analyst']);

export const decodeCsvText = (buffer: ArrayBuffer): string => {
	try {
		return new TextDecoder('utf-8', { fatal: true }).decode(buffer);
	} catch {
		return new TextDecoder('windows-1252').decode(buffer);
	}
};

type ParsedCsvRow = {
	fields: string[];
	rowNumber: number;
};

export const parseCsvUserRows = (csv: string): CsvUserImportResult => {
	const normalized = csv.replace(/^\uFEFF/, '');
	const parsedRows: ParsedCsvRow[] = [];
	let currentField = '';
	let currentRow: string[] = [];
	let inQuotes = false;
	let quotedFieldClosed = false;
	let rowNumber = 1;
	let rowStartNumber = 1;

	const finalizeField = () => {
		currentRow.push(currentField);
		currentField = '';
		quotedFieldClosed = false;
	};

	const finalizeRow = () => {
		if (currentRow.length > 0 || currentField.length > 0) {
			finalizeField();
			if (currentRow.some((field) => field.trim() !== '')) {
				parsedRows.push({ fields: currentRow, rowNumber: rowStartNumber });
			}
			currentRow = [];
		}
	};

	for (let i = 0; i < normalized.length; i += 1) {
		const char = normalized[i];

		if (char === '"') {
			if (inQuotes) {
				if (normalized[i + 1] === '"') {
					currentField += '"';
					i += 1;
				} else {
					inQuotes = false;
					quotedFieldClosed = true;
				}
			} else if (currentField.length === 0 && !quotedFieldClosed) {
				inQuotes = true;
			} else {
				return { rows: [], errors: [{ rowNumber, message: 'invalid quote usage.' }] };
			}
			continue;
		}

		if (char === ',' && !inQuotes) {
			finalizeField();
			continue;
		}

		if (char === '\n' || char === '\r') {
			if (char === '\r' && normalized[i + 1] === '\n') {
				i += 1;
			}
			if (inQuotes) {
				currentField += char;
				if (char === '\r' && normalized[i] === '\n') currentField += '\n';
				rowNumber += 1;
				continue;
			}
			finalizeRow();
			rowNumber += 1;
			rowStartNumber = rowNumber;
			continue;
		}

		if (quotedFieldClosed) {
			return { rows: [], errors: [{ rowNumber, message: 'invalid quote usage.' }] };
		}

		currentField += char;
	}

	if (inQuotes) {
		return {
			rows: [],
			errors: [{ rowNumber: rowStartNumber, message: 'unterminated quoted field.' }]
		};
	}

	if (currentField.length > 0 || currentRow.length > 0) {
		finalizeRow();
	}

	if (parsedRows.length === 0) {
		return { rows: [], errors: [{ rowNumber: 1, message: 'missing header row.' }] };
	}

	const [header, ...dataRows] = parsedRows;
	const normalizedHeader = header.fields.map((value) => value.trim().toLowerCase());
	const expectedHeaders = ['name', 'email', 'password', 'role'];
	if (
		normalizedHeader.length !== expectedHeaders.length ||
		expectedHeaders.some((value) => !normalizedHeader.includes(value)) ||
		new Set(normalizedHeader).size !== normalizedHeader.length
	) {
		return {
			rows: [],
			errors: [{ rowNumber: header.rowNumber, message: 'invalid or missing headers.' }]
		};
	}

	const headerIndex = Object.fromEntries(normalizedHeader.map((value, index) => [value, index]));
	const rows: CsvUserRow[] = [];
	const errors: CsvUserImportError[] = [];

	for (const row of dataRows) {
		if (row.fields.length !== header.fields.length) {
			errors.push({
				rowNumber: row.rowNumber,
				message: `expected ${header.fields.length} columns but found ${row.fields.length}.`
			});
			continue;
		}

		const name = row.fields[headerIndex.name].trim();
		const email = row.fields[headerIndex.email].trim();
		const password = row.fields[headerIndex.password].trim();
		const role = row.fields[headerIndex.role].trim().toLowerCase();
		if (!name) errors.push({ rowNumber: row.rowNumber, message: 'missing name.' });
		if (!email) errors.push({ rowNumber: row.rowNumber, message: 'missing email.' });
		if (!password) errors.push({ rowNumber: row.rowNumber, message: 'missing password.' });
		if (!role) errors.push({ rowNumber: row.rowNumber, message: 'missing role.' });
		if (role && !VALID_ROLES.has(role)) {
			errors.push({ rowNumber: row.rowNumber, message: `invalid role "${role}".` });
		}

		if (name && email && password && VALID_ROLES.has(role)) {
			rows.push({ rowNumber: row.rowNumber, name, email, password, role });
		}
	}

	return { rows, errors };
};
