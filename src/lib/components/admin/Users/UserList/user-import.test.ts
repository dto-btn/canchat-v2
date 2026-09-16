import { describe, expect, it } from 'vitest';
import { decodeCsvText, parseCsvUserRows } from './user-import';

describe('CSV user import', () => {
	it('preserves accented and special characters in imported names', () => {
		const csv = [
			'name,email,password,role',
			'"José García, PhD","jose.garcia@example.com","P@ssw0rd!","user"',
			'"Müller & Co.","mueller.co@example.com","Passw0rd","admin"',
			'"李小龍","li.xiao.long@example.com","Āßç123","pending"',
			'"Ana 🧪","ana@example.com","emoji123","user"'
		].join('\n');

		expect(parseCsvUserRows(csv).rows).toEqual([
			{
				rowNumber: 2,
				name: 'José García, PhD',
				email: 'jose.garcia@example.com',
				password: 'P@ssw0rd!',
				role: 'user'
			},
			{
				rowNumber: 3,
				name: 'Müller & Co.',
				email: 'mueller.co@example.com',
				password: 'Passw0rd',
				role: 'admin'
			},
			{
				rowNumber: 4,
				name: '李小龍',
				email: 'li.xiao.long@example.com',
				password: 'Āßç123',
				role: 'pending'
			},
			{
				rowNumber: 5,
				name: 'Ana 🧪',
				email: 'ana@example.com',
				password: 'emoji123',
				role: 'user'
			}
		]);
	});

	it('keeps UTF-8 encoded names intact when the CSV is uploaded as a text blob', () => {
		const csv =
			'name,email,password,role\n"François Élodie","francois@example.com","Sécure!123","user"';

		const rows = parseCsvUserRows(csv).rows;
		expect(rows[0].name).toBe('François Élodie');
		expect(rows[0].email).toBe('francois@example.com');
		expect(rows[0].password).toBe('Sécure!123');
	});

	it('reports invalid records while retaining valid rows and original row numbers', () => {
		const result = parseCsvUserRows(
			[
				'name,email,password,role',
				'Ada,ada@example.com,secret,user',
				',missing-name@example.com,secret,user',
				'NoEmail,,secret,user',
				'Grace,grace@example.com,secret,manager',
				'Lin,lin@example.com,,admin',
				'Bert,bert@example.com,secret,',
				'Extra,extra@example.com,secret,user,ignored',
				'Alan,alan@example.com,secret,analyst'
			].join('\n')
		);

		expect(result.rows.map((row) => row.rowNumber)).toEqual([2, 9]);
		expect(result.errors).toEqual([
			{ rowNumber: 3, message: 'missing name.' },
			{ rowNumber: 4, message: 'missing email.' },
			{ rowNumber: 5, message: 'invalid role "manager".' },
			{ rowNumber: 6, message: 'missing password.' },
			{ rowNumber: 7, message: 'missing role.' },
			{ rowNumber: 8, message: 'expected 4 columns but found 5.' }
		]);
	});

	it('rejects invalid headers and malformed rows', () => {
		expect(parseCsvUserRows('name,email,password\nAda,ada@example.com,secret')).toEqual({
			rows: [],
			errors: [{ rowNumber: 1, message: 'invalid or missing headers.' }]
		});
		expect(parseCsvUserRows('name,email,password,role\nAda,ada@example.com,secret')).toEqual({
			rows: [],
			errors: [{ rowNumber: 2, message: 'expected 4 columns but found 3.' }]
		});
	});

	it('supports CRLF, quoted commas, multiline values, and a UTF-8 BOM', () => {
		const result = parseCsvUserRows(
			'\uFEFFname,email,password,role\r\n"Ada, Lovelace",ada@example.com,"line one\r\nline two",user\r\nAlan,alan@example.com,secret,admin'
		);
		expect(result).toEqual({
			rows: [
				{
					rowNumber: 2,
					name: 'Ada, Lovelace',
					email: 'ada@example.com',
					password: 'line one\r\nline two',
					role: 'user'
				},
				{
					rowNumber: 4,
					name: 'Alan',
					email: 'alan@example.com',
					password: 'secret',
					role: 'admin'
				}
			],
			errors: []
		});
	});

	it('reports unterminated and invalid quoted fields', () => {
		expect(parseCsvUserRows('name,email,password,role\n"Ada,ada@example.com,secret,user')).toEqual({
			rows: [],
			errors: [{ rowNumber: 2, message: 'unterminated quoted field.' }]
		});
		expect(parseCsvUserRows('name,email,password,role\nAd"a,ada@example.com,secret,user')).toEqual({
			rows: [],
			errors: [{ rowNumber: 2, message: 'invalid quote usage.' }]
		});
	});

	it('decodes strict UTF-8 and falls back to Windows-1252', () => {
		expect(decodeCsvText(new TextEncoder().encode('José').buffer)).toBe('José');
		expect(decodeCsvText(new Uint8Array([0x4a, 0x6f, 0x73, 0xe9]).buffer)).toBe('José');
	});
});
