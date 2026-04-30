<script lang="ts">
	import { getI18n } from '$lib/utils/context';

	import { DropdownMenu } from 'bits-ui';
	import { flyAndScale } from '$lib/utils/transitions';
	import { tick } from 'svelte';

	import { config, user, tools as _tools, mobile, settings } from '$lib/stores';
	import { getTools } from '$lib/apis/tools';
	import { getToolTooltipContent, getMCPToolName } from '$lib/utils/mcp-tools';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import DocumentArrowUpSolid from '$lib/components/icons/DocumentArrowUpSolid.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import GlobeAltSolid from '$lib/components/icons/GlobeAltSolid.svelte';
	import BookOpen from '$lib/components/icons/BookOpen.svelte';
	import WrenchSolid from '$lib/components/icons/WrenchSolid.svelte';
	import Cog6Solid from '$lib/components/icons/Cog6Solid.svelte';
	import CameraSolid from '$lib/components/icons/CameraSolid.svelte';
	import PhotoSolid from '$lib/components/icons/PhotoSolid.svelte';

	const i18n = getI18n();

	// Static references for i18next-parser - DO NOT REMOVE
	// These ensure the parser finds the dynamic translation keys
	const _ensureTranslationKeys = () => {
		// Time tool translations (using actual English text as keys)
		$i18n.t('MCP: Current Time');
		$i18n.t('Get current date and time in any timezone');

		// News tool translations
		$i18n.t('MCP: News Headlines');
		$i18n.t('Get latest news headlines from around the world');

		// MPO SharePoint tool translations
		$i18n.t('MCP: MPO SharePoint');
		$i18n.t('MCP: MPO SharePoint (By ID)');
		$i18n.t('Fast search MPO SharePoint documents');
		$i18n.t('List files in MPO SharePoint folders');
		$i18n.t('Retrieve MPO SharePoint document by ID from search results');
		$i18n.t('Search and retrieve MPO SharePoint documents');

		// PMO SharePoint tool translations
		$i18n.t('MCP: PMO SharePoint');
		$i18n.t('MCP: PMO SharePoint (By ID)');
		$i18n.t('Fast search PMO SharePoint documents');
		$i18n.t('List files in PMO SharePoint folders');
		$i18n.t('Retrieve PMO SharePoint document by ID from search results');
		$i18n.t('Search and retrieve PMO SharePoint documents');
		$i18n.t('MCP tools disabled - Web Search is active');
		$i18n.t('MCP tools disabled - Wiki Grounding is active');
	};

	export let screenCaptureHandler: Function;
	export let uploadFilesHandler: Function;
	export let uploadGoogleDriveHandler: Function;

	export let selectedToolIds: string[] = [];

	export let webSearchEnabled: boolean;
	export let wikiGroundingEnabled: boolean;
	export let wikiGroundingMode: string = 'off'; // 'off', 'on'
	export let imageGenerationEnabled: boolean;

	export let onClose: Function;

	// Reactive statement for tooltip content
	$: tooltipContent = (() => {
		if (webSearchEnabled) {
			return $i18n.t('Wiki Grounding disabled - Web Search is active');
		} else if (hasMcpToolsEnabled) {
			return $i18n.t('Wiki Grounding disabled - MCP tools are active');
		} else if (wikiGroundingEnabled) {
			return $i18n.t('Wikipedia Grounding: Context-aware information enhancement enabled');
		} else {
			return $i18n.t('Wikipedia Grounding: Click to enable context-aware information enhancement');
		}
	})();

	let tools = {};
	let wikiGroundingTooltip;
	let wikiGroundingButton;
	let show = false;

	let showImageGeneration = false;
	const hoverOnlyTooltipOptions = { trigger: 'mouseenter' };

	$: showImageGeneration =
		$config?.features?.enable_image_generation &&
		($user.role === 'admin' || $user?.permissions?.features?.image_generation);

	let showWebSearch = false;

	$: showWebSearch =
		$config?.features?.enable_web_search &&
		($user.role === 'admin' || $user?.permissions?.features?.web_search);

	let showWikiGrounding = false;

	$: showWikiGrounding = $config?.features?.enable_wiki_grounding || false;
	$settings?.wikipediaGrounding ?? true;

	// Check if any MCP tools are enabled
	$: hasMcpToolsEnabled = Object.keys(tools).some(
		(toolId) => tools[toolId].isMcp && tools[toolId].enabled
	);

	$: if (show) {
		init();
	}

	const init = async () => {
		if ($_tools === null) {
			await _tools.set(await getTools(localStorage.token));
		}

		tools = $_tools.reduce((a, tool, i, arr) => {
			a[tool.id] = {
				name: tool.name,
				originalDescription: tool.meta.description, // Keep original
				enabled: selectedToolIds.includes(tool.id),
				isMcp: tool.meta?.manifest?.is_mcp_tool || false,
				originalName: tool.meta?.manifest?.original_name || tool.name, // Get the actual tool function name
				meta: tool.meta // Keep full meta object for tooltip
			};
			return a;
		}, {});
	};

	// Function to update tooltip content while keeping it visible
	const updateTooltipContent = () => {
		if (wikiGroundingTooltip && wikiGroundingTooltip._tippy) {
			wikiGroundingTooltip._tippy.setContent(tooltipContent);
		}
	};

	// Watch for tooltip content changes and update immediately
	$: if (tooltipContent && wikiGroundingTooltip) {
		updateTooltipContent();
	}

	const clearSelectedToolIds = () => {
		selectedToolIds = [];
		Object.keys(tools).forEach((toolId) => {
			tools[toolId].enabled = false;
		});
	};
</script>

<Dropdown
	bind:show
	on:change={(e) => {
		if (e.detail === false) {
			onClose();
		}
	}}
>
	<Tooltip content={$i18n.t('More')}>
		<div
			aria-label="More"
			role="button"
			class="flex bg-transparent hover:bg-white/80 text-gray-800 dark:text-white dark:hover:bg-gray-800 transition rounded-full p-2 outline-none"
		>
			<slot />
		</div>
	</Tooltip>

	<div slot="content">
		<DropdownMenu.Content
			class={`w-full max-w-[280px] rounded-xl px-1 py-1 border-gray-300/30 dark:border-gray-700/50 z-50 bg-white dark:bg-gray-850 dark:text-white shadow`}
			sideOffset={15}
			alignOffset={-8}
			side="top"
			align="start"
			transition={flyAndScale}
		>
			{#if Object.keys(tools).length > 0}
				<div
					class="max-h-[10rem] overflow-y-auto scrollbar-thin scrollbar-thumb-gray-300 dark:scrollbar-thumb-gray-600 scrollbar-track-transparent hover:scrollbar-thumb-gray-400 dark:hover:scrollbar-thumb-gray-500"
				>
					{#each Object.keys(tools) as toolId}
						<Tooltip
							content={tools[toolId].isMcp && webSearchEnabled
								? $i18n.t('MCP tools disabled - Web Search is active')
								: tools[toolId].isMcp && wikiGroundingEnabled
									? $i18n.t('MCP tools disabled - Wiki Grounding is active')
									: getToolTooltipContent(tools[toolId], $i18n)}
							placement="right"
							className="w-full"
							tippyOptions={hoverOnlyTooltipOptions}
						>
							<button
								role="menuitem"
								aria-label={tools[toolId].isMcp
									? getMCPToolName(
											tools[toolId].meta?.manifest?.original_name || tools[toolId].name,
											$i18n
										)
									: tools[toolId].name}
								class="flex w-full justify-between gap-2 items-center px-3 py-2 text-sm font-medium cursor-pointer rounded-xl {tools[
									toolId
								].isMcp &&
								(webSearchEnabled || wikiGroundingEnabled)
									? 'opacity-50 cursor-not-allowed'
									: ''}"
								on:click={() => {
									if (tools[toolId].isMcp && (webSearchEnabled || wikiGroundingEnabled)) {
										return; // Don't allow toggling MCP tools when web search or wiki grounding is active
									}
									tools[toolId].enabled = !tools[toolId].enabled;
									// If enabling an MCP tool, disable web search and wiki grounding
									if (tools[toolId].isMcp && tools[toolId].enabled) {
										webSearchEnabled = false;
										wikiGroundingEnabled = false;
										wikiGroundingMode = 'off';
									}
								}}
							>
								<div class="flex-1 flex gap-2 items-center">
									<div class="flex-shrink-0">
										{#if tools[toolId].isMcp}
											<Cog6Solid />
										{:else}
											<WrenchSolid />
										{/if}
									</div>

									<div class="flex flex-col items-start min-w-0 flex-1">
										<div class="text-sm font-medium leading-tight">
											{#if tools[toolId].isMcp}
												{getMCPToolName(tools[toolId].originalName, $i18n)}
											{:else}
												{tools[toolId].name}
											{/if}
										</div>
									</div>
								</div>

								<div class=" flex-shrink-0">
									<Switch
										state={tools[toolId].enabled}
										ariaLabel={tools[toolId].isMcp
											? `${$i18n.t('Toggle')} ${getMCPToolName(tools[toolId].meta?.manifest?.original_name || tools[toolId].name, $i18n)}`
											: `${$i18n.t('Toggle')} ${tools[toolId].name}`}
										disabled={tools[toolId].isMcp && (webSearchEnabled || wikiGroundingEnabled)}
										on:change={async (e) => {
											if (tools[toolId].isMcp && (webSearchEnabled || wikiGroundingEnabled)) {
												return; // Don't allow toggling MCP tools
											}
											const state = e.detail;
											await tick();
											if (state) {
												selectedToolIds = [...selectedToolIds, toolId];
												// If enabling an MCP tool, disable web search and wiki grounding
												if (tools[toolId].isMcp) {
													webSearchEnabled = false;
													wikiGroundingEnabled = false;
													wikiGroundingMode = 'off';
												}
											} else {
												selectedToolIds = selectedToolIds.filter((id) => id !== toolId);
											}
										}}
									/>
								</div>
							</button>
						</Tooltip>
					{/each}
				</div>

				<hr class="border-black/5 dark:border-white/5 my-1" />
			{/if}

			{#if showImageGeneration}
				<Tooltip
					content={$i18n.t('Image Generation')}
					placement="right"
					tippyOptions={hoverOnlyTooltipOptions}
				>
					<button
						role="menuitem"
						class="flex w-full justify-between gap-2 items-center px-3 py-2 text-sm font-medium cursor-pointer rounded-xl"
						on:click={() => {
							imageGenerationEnabled = !imageGenerationEnabled;
						}}
					>
						<div class="flex-1 flex items-center gap-2">
							<PhotoSolid />
							<div class=" line-clamp-1">{$i18n.t('Image')}</div>
						</div>

						<Switch state={imageGenerationEnabled} ariaLabel={$i18n.t('Toggle Image Generation')} />
					</button>
				</Tooltip>
			{/if}

			{#if showWebSearch}
				<Tooltip
					content={wikiGroundingEnabled
						? $i18n.t('Web Search disabled - Wiki Grounding is active')
						: hasMcpToolsEnabled
							? $i18n.t('Web Search disabled - MCP tools are active')
							: $i18n.t('Web Search (Beta)')}
					placement="right"
					tippyOptions={hoverOnlyTooltipOptions}
				>
					<button
						role="menuitem"
						class="flex w-full justify-between gap-2 items-center px-3 py-2 text-sm font-medium cursor-pointer rounded-xl {wikiGroundingEnabled ||
						hasMcpToolsEnabled
							? 'opacity-50 cursor-not-allowed'
							: ''}"
						disabled={wikiGroundingEnabled || hasMcpToolsEnabled}
						on:click={() => {
							if (!wikiGroundingEnabled && !hasMcpToolsEnabled) {
								webSearchEnabled = !webSearchEnabled;
								if (webSearchEnabled) {
									clearSelectedToolIds();
									wikiGroundingEnabled = false;
									wikiGroundingMode = 'off';
								}
							}
						}}
					>
						<div class="flex-1 flex items-center gap-2">
							<GlobeAltSolid />
							<div class="line-clamp-1">
								{$i18n.t('Web Search (Beta)')}
							</div>
						</div>

						<Switch
							state={webSearchEnabled}
							ariaLabel={$i18n.t('Toggle Web Search')}
							disabled={wikiGroundingEnabled || hasMcpToolsEnabled}
						/>
					</button>
				</Tooltip>
			{/if}

			{#if showWikiGrounding}
				<Tooltip
					bind:this={wikiGroundingTooltip}
					content={tooltipContent}
					placement="right"
					className="w-full"
					tippyOptions={{
						placement: 'right',
						offset: [0, 0],
						flip: false,
						hideOnClick: false,
						trigger: 'mouseenter',
						getReferenceClientRect: () => {
							const menu = document.querySelector('[data-melt-dropdown-menu][data-state="open"]');
							if (menu && wikiGroundingButton) {
								const menuRect = menu.getBoundingClientRect();
								const buttonRect = wikiGroundingButton.getBoundingClientRect();
								return {
									width: 0,
									height: buttonRect.height,
									top: buttonRect.top,
									bottom: buttonRect.bottom,
									left: menuRect.right,
									right: menuRect.right
								};
							}
							return { width: 0, height: 0, top: 0, bottom: 0, left: 0, right: 0 };
						}
					}}
				>
					<button
						role="menuitem"
						aria-label={$i18n.t('Wiki Grounding')}
						bind:this={wikiGroundingButton}
						class="flex w-full justify-between gap-2 items-center px-3 py-2 text-sm font-medium cursor-pointer rounded-xl {webSearchEnabled ||
						hasMcpToolsEnabled
							? 'opacity-50 cursor-not-allowed'
							: ''}"
						disabled={webSearchEnabled || hasMcpToolsEnabled}
						on:click={() => {
							if (!webSearchEnabled && !hasMcpToolsEnabled) {
								// Simple toggle: off -> on -> off
								if (wikiGroundingEnabled) {
									wikiGroundingMode = 'off';
									wikiGroundingEnabled = false;
								} else {
									clearSelectedToolIds();
									wikiGroundingMode = 'on';
									wikiGroundingEnabled = true;
									webSearchEnabled = false;
								}
							}
						}}
					>
						<div class="flex-1 flex items-center gap-2">
							<BookOpen />
							<div class="line-clamp-1">
								{$i18n.t('Wiki Grounding')}
							</div>
						</div>

						<div class="flex items-center">
							<Switch
								state={wikiGroundingEnabled}
								ariaLabel={$i18n.t('Toggle Wiki Grounding')}
								disabled={webSearchEnabled || hasMcpToolsEnabled}
							/>
						</div>
					</button>
				</Tooltip>
			{/if}

			{#if showImageGeneration || showWebSearch || showWikiGrounding}
				<hr class="border-black/5 dark:border-white/5 my-1" />
			{/if}

			{#if !$mobile}
				<DropdownMenu.Item
					class="flex gap-2 items-center px-3 py-2 text-sm  font-medium cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800  rounded-xl"
					on:click={() => {
						screenCaptureHandler();
					}}
				>
					<CameraSolid />
					<div class=" line-clamp-1">{$i18n.t('Capture')}</div>
				</DropdownMenu.Item>
			{/if}

			<DropdownMenu.Item
				class="flex gap-2 items-center px-3 py-2 text-sm font-medium cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl"
				on:click={() => {
					uploadFilesHandler();
				}}
			>
				<DocumentArrowUpSolid />
				<div class="line-clamp-1">{$i18n.t('Upload Files')}</div>
			</DropdownMenu.Item>

			{#if $config?.features?.enable_google_drive_integration}
				<DropdownMenu.Item
					class="flex gap-2 items-center px-3 py-2 text-sm font-medium cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl"
					on:click={() => {
						uploadGoogleDriveHandler();
					}}
				>
					<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 87.3 78" class="w-5 h-5">
						<path
							d="m6.6 66.85 3.85 6.65c.8 1.4 1.95 2.5 3.3 3.3l13.75-23.8h-27.5c0 1.55.4 3.1 1.2 4.5z"
							fill="#0066da"
						/>
						<path
							d="m43.65 25-13.75-23.8c-1.35.8-2.5 1.9-3.3 3.3l-25.4 44a9.06 9.06 0 0 0 -1.2 4.5h27.5z"
							fill="#00ac47"
						/>
						<path
							d="m73.55 76.8c1.35-.8 2.5-1.9 3.3-3.3l1.6-2.75 7.65-13.25c.8-1.4 1.2-2.95 1.2-4.5h-27.502l5.852 11.5z"
							fill="#ea4335"
						/>
						<path
							d="m43.65 25 13.75-23.8c-1.35-.8-2.9-1.2-4.5-1.2h-18.5c-1.6 0-3.15.45-4.5 1.2z"
							fill="#00832d"
						/>
						<path
							d="m59.8 53h-32.3l-13.75 23.8c1.35.8 2.9 1.2 4.5 1.2h50.8c1.6 0 3.15-.45 4.5-1.2z"
							fill="#2684fc"
						/>
						<path
							d="m73.4 26.5-12.7-22c-.8-1.4-1.95-2.5-3.3-3.3l-13.75 23.8 16.15 28h27.45c0-1.55-.4-3.1-1.2-4.5z"
							fill="#ffba00"
						/>
					</svg>
					<div class="line-clamp-1">{$i18n.t('Google Drive')}</div>
				</DropdownMenu.Item>
			{/if}
		</DropdownMenu.Content>
	</div>
</Dropdown>
