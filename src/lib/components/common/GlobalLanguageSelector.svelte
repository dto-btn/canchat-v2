<script lang="ts">
	import { getI18n } from '$lib/utils/context';
	import { updateUserSettings, getUserSettings } from '$lib/apis/users';
	import { user } from '$lib/stores';

	import Tooltip from './Tooltip.svelte';

	const i18n = getI18n();

	async function toggleLanguage() {
		const newLocale = $i18n.language === 'en-GB' ? 'fr-CA' : 'en-GB';
		await $i18n.changeLanguage(newLocale);

		// Persist language preference to backend
		if ($user?.token) {
			try {
				const currentSettings = (await getUserSettings($user.token)) || {};
				const updatedSettings = {
					...currentSettings,
					ui: {
						...(currentSettings.ui || {}),
						default_locale: newLocale
					}
				};
				await updateUserSettings($user.token, updatedSettings);
			} catch (error) {
				console.error('Failed to update language preference:', error);
			}
		}
	}

	$: currentLangDisplay = $i18n.language === 'en-GB' ? 'FR' : 'EN';
</script>

<Tooltip content={$i18n.language === 'en-GB' ? 'Français' : 'English'}>
	<button
		class="group flex cursor-pointer p-2 rounded-xl transition hover:bg-gray-50 dark:hover:bg-gray-850"
		on:click={toggleLanguage}
	>
		<div
			class="m-auto self-center text-sm font-medium text-gray-900 dark:text-white rounded transition"
		>
			{currentLangDisplay}
		</div>
	</button>
</Tooltip>
