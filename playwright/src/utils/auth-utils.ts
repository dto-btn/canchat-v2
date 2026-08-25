import type { BrowserContext } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const usersFile = path.join(__dirname, '../test-data/users.json');
export const usersData = JSON.parse(fs.readFileSync(usersFile, 'utf-8'));

export const testUsers = {
	admin: usersData.users.find((u: any) => u.username === 'admin'),
	user: usersData.users.find((u: any) => u.username === 'user'),
	analyst: usersData.users.find((u: any) => u.username === 'analyst'),
	globalAnalyst: usersData.users.find((u: any) => u.username === 'globalanalyst'),
	pending: usersData.users.find((u: any) => u.username === 'pending')
};

/**
 * Authenticates a browser context via the backend sign-in API endpoint.
 * This sets the HTTP-only refresh token session cookie in the context, ensuring
 * that when the page opens it obtains a fresh access token.
 *
 * @param context The Playwright browser context to authenticate.
 * @param user The user credentials object containing email and password.
 */
export async function authenticateContext(
	context: BrowserContext,
	user: { email: string; password: string }
): Promise<void> {
	const response = await context.request.post('/api/v1/auths/signin', {
		data: {
			email: user.email,
			password: user.password
		}
	});

	if (!response.ok()) {
		const errorBody = await response.text().catch(() => '');
		throw new Error(
			`API authentication failed for ${user.email}: [${response.status()}] ${errorBody}`
		);
	}
}
