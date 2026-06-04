<script>
	import { io } from 'socket.io-client';
	import { spring } from 'svelte/motion';

	let loadingProgress = spring(0, {
		stiffness: 0.05
	});

	import { onMount, tick, setContext, onDestroy } from 'svelte';
	import {
		ariaMessage,
		config,
		user,
		settings,
		theme,
		WEBUI_NAME,
		mobile,
		socket,
		activeUserIds,
		USAGE_POOL,
		chatId,
		chats,
		currentChatPage,
		tags,
		temporaryChatEnabled,
		isLastActiveTab,
		isApp,
		appData,
		appInfo
	} from '$lib/stores';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { Toaster, toast } from 'svelte-sonner';

	import { getBackendConfig } from '$lib/apis';
	import {
		bootstrapAuthSession,
		broadcastAuthSyncEvent,
		consumeAuthRecoveryCheckpoint,
		clearAuthRecoveryCheckpoint,
		clearAuthState,
		getAccessTokenValue,
		hydrateAuthState,
		isAuthFailure,
		isAuthTransportFailure,
		saveAuthRecoveryCheckpoint,
		subscribeToAuthSyncEvents
	} from '$lib/services/auth';
	import { accessToken } from '$lib/stores/auth';

	import '../tailwind.css';
	import '../app.css';

	import 'tippy.js/dist/tippy.css';

	import { WEBUI_BASE_URL } from '$lib/constants';
	import i18n, { initI18n, getLanguages } from '$lib/i18n';
	import { bestMatchingLanguage } from '$lib/utils';
	import { getAllTags, getChatList } from '$lib/apis/chats';
	import NotificationToast from '$lib/components/NotificationToast.svelte';
	import AppSidebar from '$lib/components/app/AppSidebar.svelte';
	import Modal from '$lib/components/common/Modal.svelte';

	setContext('i18n', i18n);

	const bc = new BroadcastChannel('active-tab-channel');
	const AUTH_SYNC_RESTORE_RETRY_DELAY_MS = 1000;
	const AUTH_SYNC_RESTORE_MAX_RETRY_DELAY_MS = 5000;

	let loaded = false;
	let authRecoveryRedirectInFlight = false;
	let authSyncRestoreInFlight = false;
	/** @type {number} */
	let authSyncRestoreRetryDelayMs = AUTH_SYNC_RESTORE_RETRY_DELAY_MS;
	/** @type {number | null} */
	let authSyncRestoreRetryTimeout = null;
	/** @type {string | null} */
	let lastSocketAuthToken = null;
	let showReauthModal = false;

	let message = '';

	const unsubscribe = ariaMessage.subscribe((value) => {
		message = value;
	});

	onDestroy(() => {
		if (authSyncRestoreRetryTimeout !== null) {
			window.clearTimeout(authSyncRestoreRetryTimeout);
		}

		unsubscribe();
	});

	const BREAKPOINT = 768;

	const reauthenticateSocket = (force = false) => {
		if (!$socket || !$socket.connected || !$accessToken) {
			return;
		}

		if (!force && lastSocketAuthToken === $accessToken) {
			return;
		}

		lastSocketAuthToken = $accessToken;
		$socket.emit('user-join', { auth: { token: $accessToken } });
	};

	const getCurrentRoutePath = () => `${$page.url.pathname}${$page.url.search}${$page.url.hash}`;

	const resetAuthSyncRestoreRetry = () => {
		if (authSyncRestoreRetryTimeout !== null) {
			window.clearTimeout(authSyncRestoreRetryTimeout);
			authSyncRestoreRetryTimeout = null;
		}

		authSyncRestoreRetryDelayMs = AUTH_SYNC_RESTORE_RETRY_DELAY_MS;
	};

	const scheduleAuthSyncRestoreRetry = () => {
		if (authSyncRestoreRetryTimeout !== null || $accessToken) {
			return;
		}

		// Double the wait time for each iteration up to the max delay
		const retryDelayMs = authSyncRestoreRetryDelayMs;
		authSyncRestoreRetryDelayMs = Math.min(
			authSyncRestoreRetryDelayMs * 2,
			AUTH_SYNC_RESTORE_MAX_RETRY_DELAY_MS
		);

		authSyncRestoreRetryTimeout = window.setTimeout(() => {
			authSyncRestoreRetryTimeout = null;
			restoreSessionFromAuthSync().catch((error) => {
				console.error('Failed to retry browser session restore:', error);
			});
		}, retryDelayMs);
	};

	const beginSessionRecovery = async (broadcast = true) => {
		if (authRecoveryRedirectInFlight || showReauthModal) {
			return;
		}

		if ($page.url.pathname === '/auth') {
			user.set(undefined);
			clearAuthState();
			return;
		}

		authRecoveryRedirectInFlight = true;
		saveAuthRecoveryCheckpoint(getCurrentRoutePath(), 'session-expired');
		showReauthModal = true;

		if (broadcast) {
			broadcastAuthSyncEvent('session-expired');
		}

		authRecoveryRedirectInFlight = false;
	};

	const handleSessionExpiry = async (broadcast = true) => {
		resetAuthSyncRestoreRetry();
		clearAuthState();

		if ($user === undefined) {
			user.set(undefined);
			if ($page.url.pathname !== '/auth') {
				await goto('/auth');
			}
			return;
		}

		await beginSessionRecovery(broadcast);
	};

	const handleLogoutNavigation = async (broadcast = true) => {
		resetAuthSyncRestoreRetry();
		showReauthModal = false;
		clearAuthRecoveryCheckpoint();

		if (broadcast) {
			broadcastAuthSyncEvent('logout');
		}

		await user.set(undefined);
		clearAuthState();

		if ($socket?.connected) {
			$socket.disconnect();
		}

		if ($page.url.pathname !== '/auth') {
			await goto('/auth');
		}
	};

	/** @param {import('$lib/stores').SessionUser} sessionUser */
	const applySessionUser = async (sessionUser, { redirectFromAuth = false } = {}) => {
		resetAuthSyncRestoreRetry();
		user.set(sessionUser);

		const accessToken = getAccessTokenValue();
		if (accessToken) {
			const { timezoneService } = await import('$lib/services/timezone');
			await timezoneService.initializeUserTimezone(accessToken).catch((error) => {
				console.warn('Failed to initialize timezone:', error);
			});
		}

		if (redirectFromAuth && $page.url.pathname === '/auth') {
			await goto(consumeAuthRecoveryCheckpoint() ?? '/');
		}
	};

	const restoreSessionFromAuthSync = async () => {
		if (authSyncRestoreInFlight || $accessToken) {
			return;
		}

		authSyncRestoreInFlight = true;
		try {
			const sessionUser = await bootstrapAuthSession();
			if (!sessionUser) {
				return;
			}

			showReauthModal = false;
			await applySessionUser(sessionUser, { redirectFromAuth: true });
		} catch (error) {
			if (isAuthFailure(error)) {
				await handleSessionExpiry(false);
			} else {
				console.error('Failed to restore browser session from auth sync:', error);
				scheduleAuthSyncRestoreRetry();
			}
		} finally {
			authSyncRestoreInFlight = false;
		}
	};

	$: if ($socket) {
		// Always update the token sent with every request
		$socket.auth = $accessToken ? { token: $accessToken } : {};

		if ($accessToken) {
			// Ensure socket is connected if we have a token
			if (!$socket.connected) {
				$socket.connect();
			} else {
				reauthenticateSocket();
			}
		} else if ($socket.connected) {
			// If no token present, disconnect the socket to prevent unauthorized access
			lastSocketAuthToken = null;
			$socket.disconnect();
		}
	}

	$: if (loaded && $user !== undefined && !$accessToken && !showReauthModal) {
		handleSessionExpiry().catch((error) => {
			console.error('Failed to start auth recovery:', error);
			authRecoveryRedirectInFlight = false;
		});
	}

	$: if ($accessToken && showReauthModal) {
		showReauthModal = false;
	}

	/** @param {boolean} enableWebsocket */
	const setupSocket = async (enableWebsocket) => {
		const _socket = io(`${WEBUI_BASE_URL}` || undefined, {
			reconnection: true,
			reconnectionDelay: 1000,
			reconnectionDelayMax: 5000,
			randomizationFactor: 0.5,
			path: '/ws/socket.io',
			transports: enableWebsocket ? ['websocket'] : ['polling', 'websocket'],
			auth: { token: getAccessTokenValue() },
			autoConnect: false
		});

		await socket.set(_socket);

		_socket.on('connect_error', (err) => {
			console.log('connect_error', err);
		});

		_socket.on('connect', () => {
			console.log('connected', _socket.id);
			lastSocketAuthToken = null;
			reauthenticateSocket(true);
		});

		_socket.on('reconnect_attempt', (attempt) => {
			console.log('reconnect_attempt', attempt);
		});

		_socket.on('reconnect_failed', () => {
			console.log('reconnect_failed');
		});

		_socket.on('disconnect', (reason, details) => {
			console.log(`Socket ${_socket.id} disconnected due to ${reason}`);
			lastSocketAuthToken = null;
			if (details) {
				console.log('Additional details:', details);
			}
		});

		_socket.on('user-list', (data) => {
			activeUserIds.set(data.user_ids);
		});

		_socket.on('usage', (data) => {
			USAGE_POOL.set(data['models']);
		});

		_socket.on('chat-deleted', async (data) => {
			console.log('Received chat deletion notification:', data);

			// Refresh chat list to reflect deleted chats
			if (data.deleted_count > 0) {
				try {
					// Update main chat list
					const updatedChats = await getChatList(undefined, $currentChatPage);
					chats.set(updatedChats);

					// Check if current chat was deleted and redirect if necessary
					if (data.deleted_chat_ids && data.deleted_chat_ids.includes($chatId)) {
						await chatId.set('');
						await goto('/');
						toast.info($i18n.t('Current chat was automatically cleaned up'));
					}

					// Show notification about cleanup
					const message =
						data.deleted_count === 1
							? $i18n.t('One chat was automatically cleaned up')
							: `${data.deleted_count} ${$i18n.t('chats were automatically cleaned up')}`;
					toast.info(message);
				} catch (error) {
					console.error('Error refreshing chats after deletion:', error);
				}
			}
		});

		_socket.on('chat-events', chatEventHandler);
		_socket.on('channel-events', channelEventHandler);
	};

	/** @param {any} event */
	const chatEventHandler = async (event) => {
		const _chat = $page.url.pathname.includes(`/c/${event.chat_id}`);

		let isFocused = document.visibilityState !== 'visible';
		if (window.electronAPI) {
			const res = await window.electronAPI.send({
				type: 'window:isFocused'
			});
			if (res) {
				isFocused = res.isFocused;
			}
		}

		if ((event.chat_id !== $chatId && !$temporaryChatEnabled) || isFocused) {
			await tick();
			const type = event?.data?.type ?? null;
			const data = event?.data?.data ?? null;

			if (type === 'chat:completion') {
				const { done, content, title } = data;

				if (done) {
					if ($isLastActiveTab) {
						if ($settings?.notificationEnabled ?? false) {
							new Notification(`${title} | Open WebUI`, {
								body: content,
								icon: `${WEBUI_BASE_URL}/static/favicon.png`
							});
						}
					}

					toast.custom(NotificationToast, {
						componentProps: {
							onClick: () => {
								goto(`/c/${event.chat_id}`);
							},
							content: content,
							title: title
						},
						duration: 15000,
						unstyled: true
					});
				}
			} else if (type === 'chat:title') {
				currentChatPage.set(1);
				await chats.set(await getChatList(undefined, $currentChatPage));
			} else if (type === 'chat:tags') {
				tags.set(await getAllTags());
			}
		}
	};

	/** @param {any} event */
	const channelEventHandler = async (event) => {
		if (event.data?.type === 'typing') {
			return;
		}

		// check url path
		const channel = $page.url.pathname.includes(`/channels/${event.channel_id}`);

		let isFocused = document.visibilityState !== 'visible';
		if (window.electronAPI) {
			const res = await window.electronAPI.send({
				type: 'window:isFocused'
			});
			if (res) {
				isFocused = res.isFocused;
			}
		}

		if ((!channel || isFocused) && event?.user?.id !== $user?.id) {
			await tick();
			const type = event?.data?.type ?? null;
			const data = event?.data?.data ?? null;

			if (type === 'message') {
				if ($isLastActiveTab) {
					if ($settings?.notificationEnabled ?? false) {
						new Notification(`${data?.user?.name} (#${event?.channel?.name}) | Open WebUI`, {
							body: data?.content,
							icon: data?.user?.profile_image_url ?? `${WEBUI_BASE_URL}/static/favicon.png`
						});
					}
				}

				toast.custom(NotificationToast, {
					componentProps: {
						onClick: () => {
							goto(`/channels/${event.channel_id}`);
						},
						content: data?.content,
						title: event?.channel?.name
					},
					duration: 15000,
					unstyled: true
				});
			}
		}
	};

	onMount(() => {
		let onResize = () => {};
		let handleVisibilityChange = () => {};
		const unsubscribeAuthSync = subscribeToAuthSyncEvents(async (event) => {
			if (event.type === 'logout') {
				await handleLogoutNavigation(false);
				return;
			}

			if (event.type === 'session-expired') {
				await handleSessionExpiry(false);
				return;
			}

			if (event.type === 'session-restored') {
				await restoreSessionFromAuthSync();
			}
		});

		const initializeLayout = async () => {
			try {
				if (window?.electronAPI) {
					const info = await window.electronAPI.send({
						type: 'app:info'
					});

					if (info) {
						isApp.set(true);
						appInfo.set(info);

						const data = await window.electronAPI.send({
							type: 'app:data'
						});

						if (data) {
							appData.set(data);
						}
					}
				}

				bc.onmessage = (event) => {
					if (event.data === 'active') {
						isLastActiveTab.set(false);
					}
				};

				handleVisibilityChange = () => {
					if (document.visibilityState === 'visible') {
						isLastActiveTab.set(true);
						bc.postMessage('active');
					}
				};

				document.addEventListener('visibilitychange', handleVisibilityChange);
				handleVisibilityChange();

				theme.set(localStorage.theme);

				mobile.set(window.innerWidth < BREAKPOINT);
				onResize = () => {
					if (window.innerWidth < BREAKPOINT) {
						mobile.set(true);
					} else {
						mobile.set(false);
					}
				};

				window.addEventListener('resize', onResize);

				hydrateAuthState();

				let backendConfig = null;
				try {
					backendConfig = await getBackendConfig();
				} catch (error) {
					console.error('Error loading backend config:', error);
				}

				initI18n(backendConfig?.default_locale);
				if (!localStorage.locale) {
					const languages = await getLanguages();
					const browserLanguages = navigator.languages?.length
						? navigator.languages
						: [navigator.language];
					const lang = backendConfig?.default_locale
						? backendConfig.default_locale
						: bestMatchingLanguage(languages, browserLanguages, 'en-GB');
					$i18n.changeLanguage(lang);
				}

				if (!backendConfig) {
					await goto('/error');
					return;
				}

				await config.set(backendConfig);
				await WEBUI_NAME.set(backendConfig.name);

				await setupSocket(backendConfig.features?.enable_websocket ?? true);

				let sessionUser = null;
				let sessionRestoreError = null;
				try {
					sessionUser = await bootstrapAuthSession();
				} catch (error) {
					sessionRestoreError = error;
					if (!isAuthFailure(error)) {
						console.error('Error restoring browser session:', error);
					}
				}

				if (sessionUser) {
					await applySessionUser(sessionUser, { redirectFromAuth: true });
				} else if (sessionRestoreError && isAuthTransportFailure(sessionRestoreError)) {
					clearAuthState();
					await user.set(undefined);
					await goto('/error');
				} else {
					clearAuthState();
					await user.set(undefined);
					clearAuthRecoveryCheckpoint();

					if ($page.url.pathname !== '/auth') {
						await goto('/auth');
					}
				}
			} catch (error) {
				console.error('Error initializing layout:', error);
				clearAuthState();
				await user.set(undefined);
				await goto('/error');
			} finally {
				await tick();

				if (
					document.documentElement.classList.contains('her') &&
					document.getElementById('progress-bar')
				) {
					loadingProgress.subscribe((value) => {
						const progressBar = document.getElementById('progress-bar');

						if (progressBar) {
							progressBar.style.width = `${value}%`;
						}
					});

					await loadingProgress.set(100);

					document.getElementById('splash-screen')?.remove();

					const audio = new Audio(`/audio/greeting.mp3`);
					const playAudio = () => {
						audio.play();
						document.removeEventListener('click', playAudio);
					};

					document.addEventListener('click', playAudio);
				} else {
					document.getElementById('splash-screen')?.remove();
				}

				loaded = true;
			}
		};

		initializeLayout();

		return () => {
			unsubscribeAuthSync();
			window.removeEventListener('resize', onResize);
			document.removeEventListener('visibilitychange', handleVisibilityChange);
			bc.onmessage = null;
		};
	});
</script>

<svelte:head>
	<title>{$WEBUI_NAME}</title>
	<link crossorigin="anonymous" rel="icon" href="{WEBUI_BASE_URL}/static/favicon.png" />

	<!-- rosepine themes have been disabled as it's not up to date with our latest version. -->
	<!-- feel free to make a PR to fix if anyone wants to see it return -->
	<!-- <link rel="stylesheet" type="text/css" href="/themes/rosepine.css" />
	<link rel="stylesheet" type="text/css" href="/themes/rosepine-dawn.css" /> -->
</svelte:head>

{#if loaded}
	{#if $isApp}
		<div class="flex flex-row h-screen">
			<AppSidebar />
			<div class="w-full flex-1 max-w-[calc(100%-4.5rem)]">
				<slot />
			</div>
		</div>
	{:else}
		<slot />
	{/if}
{/if}

<Modal bind:show={showReauthModal} size="sm" disableClose={true} title={$i18n.t('Session expired')}>
	<div class="p-6 text-black dark:text-white">
		<h2 class="text-xl font-semibold">{$i18n.t('Session expired')}</h2>
		<p class="mt-3 text-sm text-gray-600 dark:text-gray-300">
			{$i18n.t('Your session expired. Sign in again to restore your work.')}
		</p>
		<div class="mt-6 flex justify-end">
			<button
				id="reauthenticate-session-button"
				class="rounded-full bg-gray-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-black dark:bg-white dark:text-black dark:hover:bg-gray-200"
				on:click={async () => {
					showReauthModal = false;
					await user.set(undefined);
					await goto('/auth');
				}}
			>
				{$i18n.t('Sign in again')}
			</button>
		</div>
	</div>
</Modal>

<Toaster
	theme={$theme.includes('dark')
		? 'dark'
		: $theme === 'system'
			? window.matchMedia('(prefers-color-scheme: dark)').matches
				? 'dark'
				: 'light'
			: 'light'}
	richColors
	position="top-right"
	toastOptions={{
		classes: {
			error: '!bg-white !text-red-600 !border-red-600',
			success: '!bg-white !text-green-700 !border-green-700',
			warning: '!bg-white !text-yellow-700 !border-yellow-700',
			info: '!bg-white !text-blue-700 !border-blue-700'
		}
	}}
/>

<!-- ARIA live region for screen readers -->
<div aria-live="assertive" class="sr-only" role="status">{message}</div>
