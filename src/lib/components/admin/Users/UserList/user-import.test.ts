import { describe, expect, it } from 'vitest';
import { parseCsvUserRows } from './user-import';

describe('CSV user import', () => {
	it('preserves accented and special characters in imported names', () => {
		const csv = [
			'name,email,password,role',
			'"José García, PhD","jose.garcia@example.com","P@ssw0rd!","user"',
			'"Müller & Co.","mueller.co@example.com","Passw0rd","admin"',
			'"李小龍","li.xiao.long@example.com","Āßç123","pending"',
			'"Ana 🧪","ana@example.com","emoji123","user"'
		].join('\n');

		expect(parseCsvUserRows(csv)).toEqual([
			{
				name: 'José García, PhD',
				email: 'jose.garcia@example.com',
				password: 'P@ssw0rd!',
				role: 'user'
			},
			{
				name: 'Müller & Co.',
				email: 'mueller.co@example.com',
				password: 'Passw0rd',
				role: 'admin'
			},
			{
				name: '李小龍',
				email: 'li.xiao.long@example.com',
				password: 'Āßç123',
				role: 'pending'
			},
			{
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

		const rows = parseCsvUserRows(csv);
		expect(rows[0].name).toBe('François Élodie');
		expect(rows[0].email).toBe('francois@example.com');
		expect(rows[0].password).toBe('Sécure!123');
	});
});
