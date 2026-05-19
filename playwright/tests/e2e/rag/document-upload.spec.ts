import { test, expect } from '../../../src/fixtures/base-fixture';
import * as path from 'path';
import * as fs from 'fs';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const UPLOADS_DIR = path.resolve(__dirname, '../../../src/test-data/uploads');

// Helper to resolve upload paths
const uploadPath = (filename: string) => path.resolve(UPLOADS_DIR, filename);

test.describe('Feature: Document Upload and Retrieval', () => {
	test.setTimeout(120000);

	// ===========================================
	// CHAT-UPLOAD-TC001: Unsupported file type rejected
	// ===========================================
	test('CHAT-UPLOAD-TC001: User cannot upload unsupported file types', async ({
		userPage,
		locale
	}) => {
		// Upload a supported document first
		const supportedFile = uploadPath('pg11.txt');
		if (!fs.existsSync(supportedFile)) throw new Error(`Test File not found: ${supportedFile}`);
		await userPage.uploadFile(supportedFile);
		await userPage.waitForUploadComplete('document');

		const toastAppeared = await userPage.checkToastAppeared('File uploaded successfully');
		expect(toastAppeared).toBe(true);

		// Attempt to upload an unsupported document
		const unsupportedFile = uploadPath('unsupported_file.exe');
		if (!fs.existsSync(unsupportedFile)) throw new Error(`Test File not found: ${unsupportedFile}`);
		await userPage.uploadFile(unsupportedFile);
		await userPage.waitForUploadComplete('document');

		// Observe the document is not added
		const fileCount = await userPage.getUploadedFileCount();
		expect(fileCount).toBe(1);

		// Observe toast notification regarding unsupported file
		const errorToast = await userPage.checkToastAppeared('Unsupported file type');
		expect(errorToast).toBe(true);
	});

	// ===========================================
	// CHAT-UPLOAD-TC002: Upload via drag and drop
	// ===========================================
	test('CHAT-UPLOAD-TC002: User can add a file by using drag and drop', async ({ userPage }) => {
		await userPage.waitToSettle();

		const filePath = uploadPath('pg11.txt');
		if (!fs.existsSync(filePath)) throw new Error(`Test File not found: ${filePath}`);

		// Upload by dragging file into the window
		await userPage.uploadFileViaDragDrop(filePath);

		// Observe file is being uploaded (spinning circle on the file item)
		const spinnerWasVisible = await userPage.isUploadSpinnerVisible();
		expect(spinnerWasVisible).toBe(true);

		// Wait for the spinner to disappear (upload completed)
		await userPage.waitForUploadComplete('document');

		// Verify upload success toast
		const toastAppeared = await userPage.checkToastAppeared('File uploaded successfully');
		expect(toastAppeared).toBe(true);
	});

	// ===========================================
	// CHAT-UPLOAD-TC003: Upload via UI
	// ===========================================
	test('CHAT-UPLOAD-TC003: User can add file by using the UI', async ({
		userPage,
		locale
	}, testInfo) => {
		const filePath = uploadPath('pg11.txt');
		if (!fs.existsSync(filePath)) throw new Error(`Test File not found: ${filePath}`);

		await userPage.uploadFile(filePath);
		await userPage.waitForUploadComplete('document');

		const toastAppeared = await userPage.checkToastAppeared('File uploaded successfully');
		expect(toastAppeared).toBe(true);

		await userPage.sendMessage('Summarize the uploaded document.');
		await expect(userPage.responseMessages.last()).toContainText('Gutenberg', {
			timeout: 60000
		});

		const responseText = await userPage.getLastMessageText();
		await testInfo.attach('Results', {
			body: `Chat response: ${responseText}`,
			contentType: 'text/plain'
		});
	});

	// ===========================================
	// CHAT-UPLOAD-TC004: Multiple concurrent file uploads
	// ===========================================
	test('CHAT-UPLOAD-TC004: User can add multiple files concurrently', async ({ userPage }) => {
		await userPage.goto('/');

		// Upload one file
		const file1 = uploadPath('recipe_part1.txt');
		if (!fs.existsSync(file1)) throw new Error(`Test File not found: ${file1}`);
		await userPage.uploadFile(file1);
		await userPage.waitForUploadComplete('document');

		// Verify file count is 1
		const count1 = await userPage.getUploadedFileCount();
		expect(count1).toBe(1);

		// Upload two more files at once
		const file2 = uploadPath('recipe_part2.txt');
		const file3 = uploadPath('recipe_part3.txt');
		await userPage.uploadMultipleFiles([file2, file3]);
		await userPage.waitForUploadComplete('document');

		// Verify file count is 3
		const count3 = await userPage.getUploadedFileCount();
		expect(count3).toBe(3);
	});

	// ===========================================
	// CHAT-UPLOAD-TC005: Upload progress indicator
	// ===========================================
	test('CHAT-UPLOAD-TC005: User see a display indication of the upload progress', async ({
		userPage
	}) => {
		await userPage.goto('/');

		const filePath = uploadPath('pg11.txt');
		if (!fs.existsSync(filePath)) throw new Error(`Test File not found: ${filePath}`);

		await userPage.uploadFile(filePath);

		// Verify a progress indicator (spinning circle) is visible during upload
		const spinnerWasVisible = await userPage.isUploadSpinnerVisible();
		expect(spinnerWasVisible).toBe(true);

		// Wait for the spinner to disappear (upload completed)
		await userPage.waitForUploadComplete('document');
	});

	// ===========================================
	// CHAT-UPLOAD-TC006: Cancel upload while in progress
	// ===========================================
	test.skip('CHAT-UPLOAD-TC006: User cancel his document upload while it is uploading', async ({
		userPage
	}) => {
		await userPage.goto('/');

		const filePath = uploadPath('pg11.txt');
		if (!fs.existsSync(filePath)) throw new Error(`Test File not found: ${filePath}`);

		await userPage.uploadFile(filePath);
		await userPage.waitForUploadComplete('document');

		// NOTE: Race Condition
		//await userPage.removeUploadedFile(0);
		//const fileCount = await userPage.getUploadedFileCount();
		//expect(fileCount).toBe(0);
	});

	// ===========================================
	// CHAT-UPLOAD-TC007: Remove uploaded doc before submitting and add it during the conversation
	// ===========================================
	test('CHAT-UPLOAD-TC007: User can remove an uploaded document before submitting the query and add it later', async ({
		userPage
	}) => {
		await userPage.goto('/');

		// Upload one file
		const filePath = uploadPath('pg11.txt');
		if (!fs.existsSync(filePath)) throw new Error(`Test File not found: ${filePath}`);
		await userPage.uploadFile(filePath);
		await userPage.waitForUploadComplete('document');
		await userPage.checkToastAppeared('File uploaded successfully');

		// Verify file count is 1
		const count = await userPage.getUploadedFileCount();
		expect(count).toBe(1);

		// Remove the document
		await userPage.removeUploadedFile(0);
		const countAfter = await userPage.getUploadedFileCount();
		expect(countAfter).toBe(0);

		// Send query and observe response is received (without document context)
		await userPage.sendMessage('What was the uploaded document about?');
		const answerText = await userPage.getLastMessageText();
		expect(answerText).not.toBe('');

		// Upload the document again during the conversation
		await userPage.uploadFile(filePath);
		await userPage.waitForUploadComplete('document');
		await userPage.checkToastAppeared('File uploaded successfully');

		// Verify file count is 1 again
		const countReuploaded = await userPage.getUploadedFileCount();
		expect(countReuploaded).toBe(1);

		// Send query with the document
		await userPage.sendMessage('Now that I uploaded it, what is it about?');
		const secondAnswerText = await userPage.getLastMessageText();
		expect(secondAnswerText).toContain('Alice');
	});

	// ===========================================
	// CHAT-UPLOAD-TC008: Re-upload same document twice
	// ===========================================
	test('CHAT-UPLOAD-TC008: User can reupload the same document twice without conflict', async ({
		userPage
	}) => {
		await userPage.goto('/');

		const filePath = uploadPath('recipe_part1.txt');
		if (!fs.existsSync(filePath)) throw new Error(`Test File not found: ${filePath}`);

		// Upload once
		await userPage.uploadFile(filePath);
		await userPage.waitForUploadComplete('document');
		await userPage.checkToastAppeared('File uploaded successfully');

		// Verify file count is 1
		expect(await userPage.getUploadedFileCount()).toBe(1);

		// Upload again
		await userPage.uploadFile(filePath);
		await userPage.waitForUploadComplete('document');

		// Verify file count is 2 (file is added twice)
		expect(await userPage.getUploadedFileCount()).toBe(2);

		// Send query and observe response
		await userPage.sendMessage('What are the ingredients listed in the uploaded files?');
		const answerText = await userPage.getLastMessageText();
		expect(answerText).not.toBe('');
	});

	// ===========================================
	// CHAT-UPLOAD-TC009: Empty (0 byte) file rejected
	// ===========================================
	test('CHAT-UPLOAD-TC009: User cannot upload an empty document (0 byte)', async ({ userPage }) => {
		await userPage.goto('/');

		const emptyFile = uploadPath('empty_file.txt');
		if (!fs.existsSync(emptyFile)) throw new Error(`Test File not found: ${emptyFile}`);

		await userPage.uploadFile(emptyFile);

		// Observe the file is not uploaded
		const fileCount = await userPage.getUploadedFileCount();
		expect(fileCount).toBe(0);

		// Observe notification that the file has not been uploaded (empty file blocked on frontend)
		const errorToast = await userPage.checkToastAppeared('You cannot upload an empty file.');
		expect(errorToast).toBe(true);
	});

	// ===========================================
	// CHAT-UPLOAD-TC010: Multi-document query (3 files, 3 types)
	// ===========================================
	test('CHAT-UPLOAD-TC010: User can ask multi document query', async ({ userPage }, testInfo) => {
		test.setTimeout(180000);
		await userPage.goto('/');

		// Upload three files (split recipe)
		const file1 = uploadPath('recipe_part1.txt');
		const file2 = uploadPath('recipe_part2.txt');
		const file3 = uploadPath('recipe_part3.txt');

		for (const f of [file1, file2, file3]) {
			if (!fs.existsSync(f)) throw new Error(`Test File not found: ${f}`);
		}

		await userPage.uploadMultipleFiles([file1, file2, file3]);
		await userPage.waitForUploadComplete('document');

		// Verify file count is 3
		expect(await userPage.getUploadedFileCount()).toBe(3);

		// Ask to retrieve and combine content from all 3 documents
		await userPage.sendMessage(
			'List all the ingredients (wet and dry) and instructions from the uploaded recipe documents.'
		);

		const answerText = await userPage.getLastMessageText();
		expect(answerText).not.toBe('');

		// Verify that all 3 uploaded documents are cited or listed at the bottom of the answer
		expect(answerText).toContain('recipe_part1.txt');
		expect(answerText).toContain('recipe_part2.txt');
		expect(answerText).toContain('recipe_part3.txt');

		await testInfo.attach('Results', {
			body: `Multi-document response: ${answerText}`,
			contentType: 'text/plain'
		});
	});

	// ===========================================
	// CHAT-UPLOAD-TC011: Image OCR text recognition
	// ===========================================
	test('CHAT-UPLOAD-TC011: User can upload an image and have text recognized through OCR', async ({
		userPage
	}, testInfo) => {
		await userPage.goto('/');

		const imagePath = uploadPath('ssc.png');
		if (!fs.existsSync(imagePath)) throw new Error(`Test File not found: ${imagePath}`);

		await userPage.uploadFile(imagePath);
		await userPage.waitForUploadComplete('image');

		// Verify file count is 1
		expect(await userPage.getUploadedFileCount()).toBe(1);

		await userPage.sendMessage('What text content can you see in the uploaded image?');
		const answerText = await userPage.getLastMessageText();
		expect(answerText).not.toBe('');

		await testInfo.attach('Results', {
			body: `OCR response: ${answerText}`,
			contentType: 'text/plain'
		});
	});

	// ===========================================
	// CHAT-UPLOAD-TC012: Long filename (200+ characters)
	// ===========================================
	test('CHAT-UPLOAD-TC012: User can upload document with long file name (200+ character)', async ({
		userPage
	}, testInfo) => {
		// Create a file with a 200+ character name at runtime
		const longName = 'a'.repeat(200) + '.txt';
		const longNamePath = testInfo.outputPath(longName);

		fs.writeFileSync(longNamePath, 'This is a test file with a very long filename.');

		try {
			await userPage.goto('/');
			await userPage.uploadFile(longNamePath);
			await userPage.waitForUploadComplete('document');

			// Verify file is uploaded and can be queried
			await userPage.checkToastAppeared('File uploaded successfully');
			await userPage.sendMessage('Summarize the uploaded document.');
			const answerText = await userPage.getLastMessageText();
			expect(answerText).not.toBe('');

			await testInfo.attach('Results', {
				body: `Long filename response: ${answerText}`,
				contentType: 'text/plain'
			});
		} finally {
			// Cleanup the generated file
			if (fs.existsSync(longNamePath)) fs.unlinkSync(longNamePath);
		}
	});

	// ===========================================
	// CHAT-UPLOAD-TC013: Special characters in filename
	// ===========================================
	test('CHAT-UPLOAD-TC013: User can upload document with weird characters (_#@!)', async ({
		userPage
	}, testInfo) => {
		// Create a file with special characters at runtime
		const specialName = 'test_file_#@!special.txt';
		const specialNamePath = testInfo.outputPath(specialName);

		fs.writeFileSync(specialNamePath, 'This is a test file with special characters in the name.');

		try {
			await userPage.goto('/');
			await userPage.uploadFile(specialNamePath);
			await userPage.waitForUploadComplete('document');

			// Verify file is uploaded and can be queried
			await userPage.checkToastAppeared('File uploaded successfully');
			await userPage.sendMessage('Summarize the uploaded document.');
			const answerText = await userPage.getLastMessageText();
			expect(answerText).not.toBe('');

			await testInfo.attach('Results', {
				body: `Special filename response: ${answerText}`,
				contentType: 'text/plain'
			});
		} finally {
			// Cleanup the generated file
			if (fs.existsSync(specialNamePath)) fs.unlinkSync(specialNamePath);
		}
	});

	// ===========================================
	// CHAT-UPLOAD-TC014: Large file context truncation via full content mode
	// ===========================================
	test('CHAT-UPLOAD-TC014: User can trigger context truncation by enabling full content mode on a large file', async ({
		userPage
	}, testInfo) => {
		test.setTimeout(180000);
		await userPage.goto('/');

		const largeFile = uploadPath('pg1259.txt');
		if (!fs.existsSync(largeFile)) throw new Error(`Test File not found: ${largeFile}`);

		// Upload the large file
		await userPage.uploadFile(largeFile);
		await userPage.waitForUploadComplete('document');
		await userPage.checkToastAppeared('File uploaded successfully');

		// Click the file chip to open the file detail modal
		await userPage.clickUploadedFileChip(0);

		// Enable "Using Entire Document" mode
		await userPage.enableFileFullContent();

		// Close the file detail modal
		await userPage.closeFileItemModal();

		// Send the query
		await userPage.sendMessage('Count the number of letter a in the uploaded document.');
		const answerText = await userPage.getLastMessageText();
		expect(answerText).not.toBe('');

		// Verify the truncation warning is displayed
		const truncationMsg = userPage.getTranslation(
			"Some content was trimmed to fit the model's limit."
		);
		await expect(userPage.page.getByText(truncationMsg)).toBeVisible();

		await testInfo.attach('Results', {
			body: `Large file response: ${answerText}`,
			contentType: 'text/plain'
		});
	});

	// ===========================================
	// CHAT-UPLOAD-TC015: Unsupported file doesn't remove existing
	// ===========================================
	test('CHAT-UPLOAD-TC015: Unsupported file upload does not remove already-attached files', async ({
		userPage
	}) => {
		await userPage.goto('/');

		// Upload a supported file
		const supportedFile = uploadPath('recipe_part1.txt');
		if (!fs.existsSync(supportedFile)) throw new Error(`Test File not found: ${supportedFile}`);
		await userPage.uploadFile(supportedFile);
		await userPage.waitForUploadComplete('document');
		await userPage.checkToastAppeared('File uploaded successfully');

		// Verify file count is 1
		expect(await userPage.getUploadedFileCount()).toBe(1);

		// Attempt to upload an unsupported file
		const unsupportedFile = uploadPath('unsupported_file.exe');
		await userPage.uploadFile(unsupportedFile);

		// Verify file count is still 1 (unsupported file was rejected)
		expect(await userPage.getUploadedFileCount()).toBe(1);

		// Send query and observe response using the supported file
		await userPage.sendMessage('What ingredients are listed?');
		const answerText = await userPage.getLastMessageText();
		expect(answerText).not.toBe('');
	});

	// ===========================================
	// CHAT-UPLOAD-TC016: File count/size limits enforced on submit
	// ===========================================
	test('CHAT-UPLOAD-TC016: User cannot submit when file count or size exceed limits', async ({
		adminPage,
		userPage
	}) => {
		test.setTimeout(180000);

		const MAX_FILE_COUNT = 5;
		const MAX_FILE_SIZE_MB = 2;

		await adminPage.navigateToAdminSettings(
			adminPage.getTranslation('Settings'),
			adminPage.getTranslation('Documents')
		);

		const limitedPlaceholder = adminPage.getTranslation('Leave empty for unlimited');

		// Set Max Upload Size to 2 MB and verify
		const maxSizeInput = adminPage.page.getByPlaceholder(limitedPlaceholder).first();
		await maxSizeInput.fill(String(MAX_FILE_SIZE_MB));
		await expect(maxSizeInput).toHaveValue(String(MAX_FILE_SIZE_MB));

		// Set Max Upload Count to 5 and verify
		const maxCountInput = adminPage.page.getByPlaceholder(limitedPlaceholder).last();
		await maxCountInput.fill(String(MAX_FILE_COUNT));
		await expect(maxCountInput).toHaveValue(String(MAX_FILE_COUNT));

		// Save settings and verify success toast
		await adminPage.saveButton.click();
		await adminPage.verifyToast('Settings saved successfully!');

		// Test size limit
		await userPage.goto('/');
		const oversizedFilePath = path.resolve(UPLOADS_DIR, '_tc016_oversized.txt');
		// Create a file slightly above the 2 MB limit
		fs.writeFileSync(oversizedFilePath, Buffer.alloc(MAX_FILE_SIZE_MB * 1024 * 1024 + 1024, 'x'));

		try {
			await userPage.uploadFile(oversizedFilePath);
			await userPage.waitToSettle(2000);

			// File should be rejected
			expect(await userPage.getUploadedFileCount()).toBe(0);

			// Verify size error toast
			const sizeErrorTemplate = userPage.getTranslation(
				'File size should not exceed {{maxSize}} MB.'
			);
			const toastErrorText = sizeErrorTemplate.split('{{maxSize}}')[0];
			const sizeErrorAppeared = await userPage.checkToastAppeared(toastErrorText);
			expect(sizeErrorAppeared).toBe(true);
		} finally {
			if (fs.existsSync(oversizedFilePath)) fs.unlinkSync(oversizedFilePath);
		}

		// Test file count limit
		await userPage.goto('/');

		const testFile = uploadPath('recipe_part1.txt');
		if (!fs.existsSync(testFile)) throw new Error(`Test File not found: ${testFile}`);

		// Upload MAX_FILE_COUNT + 1 files
		for (let i = 0; i <= MAX_FILE_COUNT; i++) {
			await userPage.uploadFile(testFile);
			await userPage.waitForUploadComplete('document');
		}

		expect(await userPage.getUploadedFileCount()).toBe(MAX_FILE_COUNT + 1);

		// Attempt to send, the count check is at submit time
		await userPage.messageInput.fill('test');
		await expect(userPage.sendButton).toBeEnabled();
		await userPage.sendButton.click();
		await userPage.waitToSettle(1000);

		// Still on the page and the prompt was not accepted.
		expect(await userPage.getUploadedFileCount()).toBe(MAX_FILE_COUNT + 1);
	});
});
