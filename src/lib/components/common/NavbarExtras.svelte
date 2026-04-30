<script lang="ts">
	import { config, showArchivedChats, user } from '$lib/stores';
	import { getContext } from 'svelte';
	import ShortcutsModal from '$lib/components/chat/ShortcutsModal.svelte';
	import QuestionMarkCircle from '$lib/components/icons/QuestionMarkCircle.svelte';
	import HelpMenu from '$lib/components/layout/Help/HelpMenu.svelte';
	import UserMenu from '$lib/components/layout/Sidebar/UserMenu.svelte';
	import GlobalLanguageSelector from '$lib/components/common/GlobalLanguageSelector.svelte';
	import IssueModal from '$lib/components/common/IssueModal.svelte';
	import SuggestionModal from '$lib/components/common/SuggestionModal.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	// Modals
	let showShortcuts = false;
	let showIssue = false;
	let showSuggestion = false;

	// Reactive Statements
	$: SurveyUrl = $i18n.language === 'fr-CA' ? $config?.survey_url_fr : $config?.survey_url;
	$: DocsUrl = $i18n.language === 'fr-CA' ? $config?.docs_url_fr : $config?.docs_url;
	$: TrainingUrl = $i18n.language === 'fr-CA' ? $config?.training_url_fr : $config?.training_url;

	// Event Handlers
	const toggleShortcuts = () => (showShortcuts = !showShortcuts);
	const openUrl = (url: string) => window.open(url, '_blank');
</script>

<div class="flex items-center gap-1 mr-1">
	<!-- Help Menu -->
	<div class="self-start flex flex-none items-center text-gray-600 dark:text-gray-400">
		<HelpMenu
			showDocsHandler={() => openUrl(DocsUrl)}
			showTrainingHandler={() => openUrl(TrainingUrl)}
			showShortcutsHandler={toggleShortcuts}
			showSurveyHandler={() => openUrl(SurveyUrl)}
			showIssueHandler={() => (showIssue = true)}
			showSuggestionHandler={() => (showSuggestion = true)}
		>
			<Tooltip content={$i18n.t('Help')} placement="bottom">
				<div
					class="flex cursor-pointer p-2 rounded-xl text-gray-900 hover:bg-gray-50 dark:text-white dark:hover:bg-gray-850 transition"
				>
					<QuestionMarkCircle className="size-5" strokeWidth="2" />
				</div>
			</Tooltip>
		</HelpMenu>
	</div>

	<!-- Language Selector -->
	<div class="self-start flex flex-none items-center text-gray-600 dark:text-gray-400">
		<GlobalLanguageSelector />
	</div>

	<!-- User Menu -->
	{#if $user}
		<div class="self-start flex flex-none items-center text-gray-600 dark:text-gray-400">
			<div
				class="select-none flex rounded-xl p-2 hover:bg-gray-50 dark:hover:bg-gray-850 transition"
			>
				<UserMenu
					role={$user.role}
					on:show={(e) => {
						if (e.detail === 'archived-chat') {
							showArchivedChats.set(true);
						}
					}}
				>
					<img
						src={$user.profile_image_url}
						class="size-5 object-cover rounded-full"
						alt="User profile"
						draggable="false"
					/>
				</UserMenu>
			</div>
		</div>
	{/if}
</div>

<!-- Modals -->
<ShortcutsModal bind:show={showShortcuts} />
<IssueModal bind:show={showIssue} />
<SuggestionModal bind:show={showSuggestion} />
