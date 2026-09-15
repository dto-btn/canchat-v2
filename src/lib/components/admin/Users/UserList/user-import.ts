export type CsvUserRow = {
	name: string;
	email: string;
	password: string;
	role: string;
};

const VALID_ROLES = new Set(['admin', 'user', 'pending', 'analyst', 'global_analyst']);

export const parseCsvUserRows = (csv: string): CsvUserRow[] => {
	const normalized = csv.replace(/^\uFEFF/, '').trim();
	if (!normalized) {
		return [];
	}

	const rows: string[][] = [];
	let currentField = '';
	let currentRow: string[] = [];
	let inQuotes = false;

	const finalizeField = () => {
		currentRow.push(currentField);
		currentField = '';
	};

	const finalizeRow = () => {
		if (currentRow.length > 0 || currentField.length > 0) {
			finalizeField();
			if (currentRow.some((field) => field.trim() !== '')) {
				rows.push(currentRow);
			}
			currentRow = [];
		}
	};

	for (let i = 0; i < normalized.length; i += 1) {
		const char = normalized[i];

		if (char === '"') {
			if (inQuotes && normalized[i + 1] === '"') {
				currentField += '"';
				i += 1;
			} else {
				inQuotes = !inQuotes;
			}
			continue;
		}

		if (char === ',' && !inQuotes) {
			finalizeField();
			continue;
		}

		if ((char === '\n' || char === '\r') && !inQuotes) {
			if (char === '\r' && normalized[i + 1] === '\n') {
				i += 1;
			}
			finalizeRow();
			continue;
		}

		currentField += char;
	}

	if (currentField.length > 0 || currentRow.length > 0) {
		finalizeRow();
	}

	if (rows.length === 0) {
		return [];
	}

	const [header, ...dataRows] = rows;
	const normalizedHeader = header.map((value) => value.trim().toLowerCase());
	const headerIndex = {
		name: normalizedHeader.indexOf('name'),
		email: normalizedHeader.indexOf('email'),
		password: normalizedHeader.indexOf('password'),
		role: normalizedHeader.indexOf('role')
	};

	if (
		headerIndex.name === -1 ||
		headerIndex.email === -1 ||
		headerIndex.password === -1 ||
		headerIndex.role === -1
	) {
		return [];
	}

	return dataRows
		.map((row) => {
			const fields = Array.from({ length: Math.max(header.length, row.length) }, (_, index) => {
				return row[index] ?? '';
			});

			const name = (fields[headerIndex.name] ?? '').trim();
			const email = (fields[headerIndex.email] ?? '').trim();
			const password = (fields[headerIndex.password] ?? '').trim();
			const role = (fields[headerIndex.role] ?? '').trim().toLowerCase();

			if (!name || !email || !password || !VALID_ROLES.has(role)) {
				return null;
			}

			return {
				name,
				email,
				password,
				role
			};
		})
		.filter((row): row is CsvUserRow => row !== null);
};
