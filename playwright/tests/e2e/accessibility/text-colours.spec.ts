import { readFile } from 'node:fs/promises';
import { test, expect } from '@playwright/test';
import postcss from 'postcss';
import tailwindcss from 'tailwindcss';
import tailwindConfig from '../../../../tailwind.config.js';

const markup = `
	<button id="hover" class="text-gray-500 hover:text-gray-700">Hover</button>
	<button id="focus" class="text-gray-400 focus:text-gray-700">Focus</button>
	<div id="group" class="group">
		<span id="group-child" class="text-gray-300 group-hover:text-gray-700">Group hover</span>
	</div>
	<span id="gray-600" class="text-gray-600">Secondary</span>
	<span id="themed" class="text-gray-500 dark:text-gray-400">Themed</span>
	<input id="input" placeholder="Input placeholder" />
	<textarea id="textarea" placeholder="Textarea placeholder"></textarea>
	<input id="rename" class="bg-transparent w-full outline-none mr-10" />
	<input id="search" class="w-full text-sm pr-4 py-1 rounded-r-xl outline-none bg-transparent" />
	<input id="reaction" class="w-full text-sm bg-transparent outline-none" />
	<select id="select"><option>Option</option></select>
	<input id="borderless" class="border-0" />
	<input id="transparent-border" class="border border-transparent" />
	<input id="bordered" class="border-2 border-gray-300 focus:border-blue-500 dark:border-gray-600 dark:focus:border-blue-400" />
`;

let styles: string;

test.beforeAll(async () => {
	const tailwindSource = await readFile('src/tailwind.css', 'utf8');
	const appSource = await readFile('src/app.css', 'utf8');
	const result = await postcss([
		tailwindcss({ ...tailwindConfig, content: [{ raw: markup, extension: 'html' }] })
	]).process(`${tailwindSource}\n${appSource}`, { from: 'src/tailwind.css' });
	styles = result.css;
});

test.beforeEach(async ({ page }) => {
	await page.setContent(markup);
	await page.addStyleTag({ content: styles });
});

test('light-mode muted text preserves interactive colours and gray-600', async ({ page }) => {
	for (const id of ['hover', 'focus', 'group-child']) {
		await expect(page.locator(`#${id}`)).toHaveCSS('color', 'rgb(89, 89, 89)');
	}
	await expect(page.locator('#gray-600')).toHaveCSS('color', 'rgb(103, 103, 103)');

	await page.locator('#hover').hover();
	await expect(page.locator('#hover')).toHaveCSS('color', 'rgb(78, 78, 78)');
	await page.locator('#focus').focus();
	await expect(page.locator('#focus')).toHaveCSS('color', 'rgb(78, 78, 78)');
	await page.locator('#group').hover();
	await expect(page.locator('#group-child')).toHaveCSS('color', 'rgb(78, 78, 78)');
});

test('dark mode preserves Tailwind text colours', async ({ page }) => {
	await page.evaluate(() => document.documentElement.classList.add('dark'));
	await expect(page.locator('#hover')).toHaveCSS('color', 'rgb(155, 155, 155)');
	await expect(page.locator('#focus')).toHaveCSS('color', 'rgb(180, 180, 180)');
	await expect(page.locator('#group-child')).toHaveCSS('color', 'rgb(205, 205, 205)');
	await expect(page.locator('#themed')).toHaveCSS('color', 'rgb(180, 180, 180)');
});

for (const { theme, color } of [
	{ theme: 'light', color: 'rgb(89, 89, 89)' },
	{ theme: 'dark', color: 'rgb(180, 180, 180)' }
]) {
	test(`${theme}-mode controls retain their explicit border styling`, async ({ page }) => {
		await page.evaluate((theme) => {
			document.documentElement.classList.toggle('dark', theme === 'dark');
		}, theme);
		for (const id of [
			'input',
			'textarea',
			'rename',
			'search',
			'reaction',
			'select',
			'borderless'
		]) {
			await expect(page.locator(`#${id}`)).toHaveCSS('border-top-width', '0px');
		}
		await expect(page.locator('#transparent-border')).toHaveCSS(
			'border-top-color',
			'rgba(0, 0, 0, 0)'
		);
		const bordered = page.locator('#bordered');
		await expect(bordered).toHaveCSS('border-top-width', '2px');
		await expect(bordered).toHaveCSS(
			'border-top-color',
			theme === 'dark' ? 'rgb(103, 103, 103)' : 'rgb(205, 205, 205)'
		);
		await bordered.focus();
		await expect(bordered).toHaveCSS(
			'border-top-color',
			theme === 'dark' ? 'rgb(96, 165, 250)' : 'rgb(59, 130, 246)'
		);
	});

	test(`${theme}-mode placeholders share the theme colour and full opacity`, async ({ page }) => {
		await page.evaluate((theme) => {
			document.documentElement.classList.toggle('dark', theme === 'dark');
		}, theme);
		for (const id of ['input', 'textarea']) {
			const placeholder = await page.locator(`#${id}`).evaluate((element) => {
				const style = getComputedStyle(element, '::placeholder');
				return { color: style.color, opacity: style.opacity };
			});
			expect(placeholder).toEqual({ color, opacity: '1' });
		}
	});
}
