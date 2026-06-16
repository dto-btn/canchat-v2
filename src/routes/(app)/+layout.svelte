<script lang="ts">
	import { getI18n } from '$lib/utils/context';

	import { onMount, tick } from 'svelte';
	import { openDB, deleteDB } from 'idb';
	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

	import { goto } from '$app/navigation';
	import { page } from '$app/stores';

	import { getModels } from '$lib/apis';
	import { getPromptsLegacy } from '$lib/apis/prompts';
	import { getTools } from '$lib/apis/tools';
	import { getBanners } from '$lib/apis/configs';
	import { getUserSettings } from '$lib/apis/users';
	import { getRequestToken } from '$lib/services/auth';
	import { authBootstrapReady } from '$lib/stores/auth';

	import {
		config,
		user,
		settings,
		models,
		prompts,
		tools,
		banners,
		showSettings,
		showChangelog,
		temporaryChatEnabled
	} from '$lib/stores';

	import Sidebar from '$lib/components/layout/Sidebar.svelte';
	import SettingsModal from '$lib/components/chat/SettingsModal.svelte';
	import ChangelogModal from '$lib/components/ChangelogModal.svelte';
	import AccountPending from '$lib/components/layout/Overlay/AccountPending.svelte';

	const i18n = getI18n();
	const APP_ROLES = ['user', 'admin', 'analyst', 'global_analyst'] as const;

	type LocalChatDatabase = Awaited<ReturnType<typeof openDB>>;
	type SettingsState = Parameters<(typeof settings)['set']>[0];
	type ModelsState = Parameters<(typeof models)['set']>[0];
	type ToolsState = Parameters<(typeof tools)['set']>[0];
	type PromptsState = Parameters<(typeof prompts)['set']>[0];

	let loaded = false;
	let DB: LocalChatDatabase | null = null;
	let localDBChats: unknown[] = [];

	const hasAppAccess = (role?: string | null) => {
		return Boolean(role && APP_ROLES.includes(role as (typeof APP_ROLES)[number]));
	};

	const readLocalStorageSettings = () => {
		try {
			return JSON.parse(localStorage.getItem('settings') ?? '{}') as SettingsState;
		} catch (error) {
			console.error('Failed to parse settings from localStorage', error);
			return {} as SettingsState;
		}
	};

	const waitForAuthBootstrap = async () => {
		if ($authBootstrapReady) {
			return;
		}

		await new Promise<void>((resolve) => {
			const unsubscribe = authBootstrapReady.subscribe((ready) => {
				if (!ready) {
					return;
				}

				unsubscribe();
				resolve();
			});
		});
	};

	onMount(async () => {
		try {
			await waitForAuthBootstrap();

			if ($user === undefined) {
				await goto('/auth');
			} else if (hasAppAccess($user.role)) {
				try {
					// Check if IndexedDB exists
					DB = await openDB('Chats', 1);

					if (DB) {
						const chats = await DB.getAllFromIndex('chats', 'timestamp');
						localDBChats = chats.map((item, idx) => chats[chats.length - 1 - idx]);

						if (localDBChats.length === 0) {
							await deleteDB('Chats');
						}
					}
				} catch (error) {
					// IndexedDB Not Found
				}

				const fallbackSettings = readLocalStorageSettings();
				const token = getRequestToken();
				const [
					userSettingsResult,
					modelsResult,
					bannersResult,
					toolsResult,
					promptsResult
				] = await Promise.allSettled([
					getUserSettings(token),
					getModels(token),
					getBanners(token),
					getTools(token),
					getPromptsLegacy(token)
				]);

				if (
					userSettingsResult.status === 'fulfilled' &&
					userSettingsResult.value &&
					typeof userSettingsResult.value === 'object' &&
					'ui' in userSettingsResult.value &&
					userSettingsResult.value.ui
				) {
					settings.set(userSettingsResult.value.ui as SettingsState);
				} else {
					if (userSettingsResult.status === 'rejected') {
						console.error(userSettingsResult.reason);
					}
					settings.set(fallbackSettings);
				}

				models.set(
					modelsResult.status === 'fulfilled' ? (modelsResult.value as ModelsState) : ([] as ModelsState)
				);
				banners.set(bannersResult.status === 'fulfilled' ? bannersResult.value : []);
				tools.set(
					toolsResult.status === 'fulfilled'
						? (toolsResult.value as ToolsState)
						: ([] as unknown as ToolsState)
				);
				prompts.set(
					promptsResult.status === 'fulfilled'
						? (promptsResult.value as PromptsState)
						: ([] as PromptsState)
				);

				document.addEventListener('keydown', async function (event) {
					const isCtrlPressed = event.ctrlKey || event.metaKey; // metaKey is for Cmd key on Mac
					// Check if the Shift key is pressed
					const isShiftPressed = event.shiftKey;

					// Check if Ctrl + Shift + O is pressed
					if (isCtrlPressed && isShiftPressed && event.key.toLowerCase() === 'o') {
						event.preventDefault();
						document.getElementById('sidebar-new-chat-button')?.click();
					}

					// Check if Shift + Esc is pressed
					if (isShiftPressed && event.key === 'Escape') {
						event.preventDefault();
						document.getElementById('chat-input')?.focus();
					}

					// Check if Ctrl + Shift + ; is pressed
					if (isCtrlPressed && isShiftPressed && event.key === ';') {
						event.preventDefault();
						const button = [...document.getElementsByClassName('copy-code-button')]?.at(-1) as
							| HTMLElement
							| undefined;
						button?.click();
					}

					// Check if Ctrl + Shift + C is pressed
					if (isCtrlPressed && isShiftPressed && event.key.toLowerCase() === 'c') {
						event.preventDefault();
						const button = [...document.getElementsByClassName('copy-response-button')]?.at(-1) as
							| HTMLElement
							| undefined;
						button?.click();
					}

					// Check if Ctrl + Shift + S is pressed
					if (isCtrlPressed && isShiftPressed && event.key.toLowerCase() === 's') {
						event.preventDefault();
						document.getElementById('sidebar-toggle-button')?.click();
					}

					// Check if Ctrl + Shift + Backspace is pressed
					if (
						isCtrlPressed &&
						isShiftPressed &&
						(event.key === 'Backspace' || event.key === 'Delete')
					) {
						event.preventDefault();
						document.getElementById('delete-chat-button')?.click();
					}

					// Check if Ctrl + . is pressed
					if (isCtrlPressed && event.key === '.') {
						event.preventDefault();
						showSettings.set(!$showSettings);
					}

					// Check if Ctrl + / is pressed
					if (isCtrlPressed && event.key === '/') {
						event.preventDefault();
						document.getElementById('show-shortcuts-button')?.click();
					}

					// Check if Ctrl + Shift + ' is pressed
					if (isCtrlPressed && isShiftPressed && event.key.toLowerCase() === `'`) {
						event.preventDefault();
						temporaryChatEnabled.set(!$temporaryChatEnabled);
						await goto('/');
						const newChatButton = document.getElementById('new-chat-button');
						setTimeout(() => {
							newChatButton?.click();
						}, 0);
					}
				});

				const extendedSettings = $settings as typeof $settings & {
					showChangelog?: boolean;
					version?: string;
				};
				if ($user.role === 'admin' && (extendedSettings.showChangelog ?? true)) {
					showChangelog.set((extendedSettings.version ?? '') !== ($config?.version ?? ''));
				}

				if ($page.url.searchParams.get('temporary-chat') === 'true') {
					temporaryChatEnabled.set(true);
				}
				await tick();
			}
		} catch (error) {
			console.error('Failed to initialize app layout', error);
		} finally {
			loaded = true;
		}
	});
</script>

<SettingsModal bind:show={$showSettings} />
<ChangelogModal bind:show={$showChangelog} />

<div class="app relative">
	<div
		class=" text-gray-700 dark:text-gray-100 bg-white dark:bg-gray-900 h-screen max-h-[100dvh] overflow-auto flex flex-row justify-end"
	>
		{#if loaded && $user}
			{#if !hasAppAccess($user.role)}
				<AccountPending />
			{:else if localDBChats.length > 0}
				<div class="fixed w-full h-full flex z-50">
					<div
						class="absolute w-full h-full backdrop-blur-md bg-white/20 dark:bg-gray-900/50 flex justify-center"
					>
						<div class="m-auto pb-44 flex flex-col justify-center">
							<div class="max-w-md">
								<div class="text-center dark:text-white text-2xl font-medium z-50">
									Important Update<br /> Action Required for Chat Log Storage
								</div>

								<div class=" mt-4 text-center text-sm dark:text-gray-200 w-full">
									{$i18n.t(
										"Saving chat logs directly to your browser's storage is no longer supported. Please take a moment to download and delete your chat logs by clicking the button below. Don't worry, you can easily re-import your chat logs to the backend through"
									)}
									<span class="font-semibold dark:text-white"
										>{$i18n.t('Settings')} > {$i18n.t('Chats')} > {$i18n.t('Import Chats')}</span
									>. {$i18n.t(
										'This ensures that your valuable chats are securely saved to your backend database. Thank you!'
									)}
								</div>

								<div class=" mt-6 mx-auto relative group w-fit">
									<button
										class="relative z-20 flex px-5 py-2 rounded-full bg-white border border-gray-100 dark:border-none hover:bg-gray-100 transition font-medium text-sm"
										on:click={async () => {
											let blob = new Blob([JSON.stringify(localDBChats)], {
												type: 'application/json'
											});
											saveAs(blob, `chat-export-${Date.now()}.json`);

											if (!DB) {
												return;
											}

											const tx = DB.transaction('chats', 'readwrite');
											await Promise.all([tx.store.clear(), tx.done]);
											await deleteDB('Chats');

											localDBChats = [];
										}}
									>
										Download & Delete
									</button>

									<button
										class="text-xs text-center w-full mt-2 text-gray-400 underline"
										on:click={async () => {
											localDBChats = [];
										}}>{$i18n.t('Close')}</button
									>
								</div>
							</div>
						</div>
					</div>
				</div>
			{/if}

			<Sidebar />
			<slot />
		{/if}
	</div>
</div>

<style>
	.loading {
		display: inline-block;
		clip-path: inset(0 1ch 0 0);
		animation: l 1s steps(3) infinite;
		letter-spacing: -0.5px;
	}

	@keyframes l {
		to {
			clip-path: inset(0 -1ch 0 0);
		}
	}

	pre[class*='language-'] {
		position: relative;
		overflow: auto;

		/* make space  */
		margin: 5px 0;
		padding: 1.75rem 0 1.75rem 1rem;
		border-radius: 10px;
	}

	pre[class*='language-'] button {
		position: absolute;
		top: 5px;
		right: 5px;

		font-size: 0.9rem;
		padding: 0.15rem;
		background-color: #828282;

		border: ridge 1px #7b7b7c;
		border-radius: 5px;
		text-shadow: #c4c4c4 0 0 2px;
	}

	pre[class*='language-'] button:hover {
		cursor: pointer;
		background-color: #bcbabb;
	}
</style>
