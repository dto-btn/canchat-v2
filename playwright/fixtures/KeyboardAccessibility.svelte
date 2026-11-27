<script lang="ts">
	import { setContext } from 'svelte';
	import { writable } from 'svelte/store';
	import { createInstance } from 'i18next';
	import en from '$lib/i18n/locales/en-GB/translation.json';
	import fr from '$lib/i18n/locales/fr-CA/translation.json';
	import {
		config,
		models,
		user,
		showControls,
		showSidebar,
		ariaMessage,
		settings,
		tools
	} from '$lib/stores';
	import { Pane, PaneGroup } from 'paneforge';
	import Message from '$lib/components/chat/Messages/Message.svelte';
	import Selector from '$lib/components/chat/ModelSelector/Selector.svelte';
	import RecursiveFolder from '$lib/components/layout/Sidebar/RecursiveFolder.svelte';
	import ChatMenu from '$lib/components/layout/Sidebar/ChatMenu.svelte';
	import ChatItem from '$lib/components/layout/Sidebar/ChatItem.svelte';
	import Collapsible from '$lib/components/common/Collapsible.svelte';
	import ChatControls from '$lib/components/chat/ChatControls.svelte';
	import { focusActiveMessage, isJumpToMessagesShortcut } from '$lib/utils/message-navigation';
	import Folder from '$lib/components/common/Folder.svelte';
	import Markdown from '$lib/components/chat/Messages/Markdown.svelte';
	import WebSearchResults from '$lib/components/chat/Messages/ResponseMessage/WebSearchResults.svelte';
	import MessageInput from '$lib/components/chat/MessageInput.svelte';
	import { Toaster } from 'svelte-sonner';
	import CodeBlock from '$lib/components/chat/Messages/CodeBlock.svelte';
	import CodeEditor from '$lib/components/common/CodeEditor.svelte';
	import SearchInput from '$lib/components/layout/Sidebar/SearchInput.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Navbar from '$lib/components/chat/Navbar.svelte';
	import ChatPlaceholder from '$lib/components/chat/ChatPlaceholder.svelte';
	import ShortcutsModal from '$lib/components/chat/ShortcutsModal.svelte';
	import Placeholder from '$lib/components/chat/Placeholder.svelte';
	import InputMenu from '$lib/components/chat/MessageInput/InputMenu.svelte';

	const locale = new URLSearchParams(location.search).get('locale') || 'en-GB';
	const controlsOnly = new URLSearchParams(location.search).has('controls');
	const touchControls = new URLSearchParams(location.search).has('touch-controls');
	const testerNotes = new URLSearchParams(location.search).has('tester-notes');
	const i18n = createInstance();
	i18n.init({
		lng: locale,
		fallbackLng: 'en-GB',
		returnEmptyString: false,
		resources: { 'en-GB': { translation: en }, 'fr-CA': { translation: fr } }
	});
	setContext('i18n', writable(i18n));
	user.set({ id: 'test', name: 'Alex', role: 'user', permissions: { chat: { controls: true } } });
	config.set({ features: {} });
	if (touchControls) settings.set({ richTextInput: false });
	const model = {
		id: 'test-model',
		name: new URLSearchParams(location.search).has('long-model')
			? 'A very long model name that should not overlap the header actions'
			: 'Test model',
		owned_by: 'openai',
		external: false
	};
	models.set([model]);

	let history = {
		currentId: 'last',
		messages: {
			first: {
				id: 'first',
				role: 'user',
				content: 'First message',
				timestamp: 1700000000,
				parentId: null,
				childrenIds: ['response'],
				models: ['test-model']
			},
			response: {
				id: 'response',
				role: 'assistant',
				content: 'Older response',
				timestamp: 1700000001,
				parentId: 'first',
				childrenIds: ['last'],
				model: 'test-model',
				done: true,
				...(testerNotes ? { usage: { total_tokens: 42 } } : {})
			},
			last: {
				id: 'last',
				role: 'user',
				content: 'Last message',
				timestamp: 1700000002,
				parentId: 'response',
				childrenIds: [],
				models: ['test-model']
			}
		}
	};
	const noop = () => {};
	let changed = 0;
	let search = '';
	let showShortcuts = false;
	let selectedModels = ['test-model'];
	let newChats = 0;
	if (testerNotes) {
		showSidebar.set(false);
		config.set({ features: { pbmm_env: true, enable_image_generation: true } });
		user.set({
			id: 'test',
			name: 'Alex',
			role: 'user',
			permissions: { chat: { controls: true }, features: { image_generation: true } }
		});
		tools.set([]);
		models.set([model, { ...model, id: 'other-model', name: 'Other model' }]);
		history.messages.response.content = '```javascript\nconst answer = 42;\n```';
	}
</script>

<Toaster />

{#if testerNotes}
	<div id="notes-header">
		<Navbar bind:selectedModels initNewChat={() => newChats++} />
	</div>
	<output aria-label="New chats">{newChats}</output>
	<button id="before-code">Before code</button>
	<CodeBlock id="notes-code" lang="javascript" code={'const answer = 42;'} />
	<button id="after-code">After code</button>
	<div id="response-with-code">
		<Message
			idx={0}
			messageId="response"
			bind:history
			chatId="notes-chat"
			user={{ name: 'Alex' }}
			showPreviousMessage={noop}
			showNextMessage={noop}
			updateChat={noop}
			editMessage={noop}
			saveMessage={noop}
			deleteMessage={noop}
			rateMessage={noop}
			actionMessage={noop}
			submitMessage={noop}
			regenerateResponse={noop}
			continueResponse={noop}
			mergeResponses={noop}
			addMessages={noop}
			triggerScroll={noop}
		/>
	</div>
	<CodeEditor id="standalone" value="standalone" />
	<div class="w-64">
		<SearchInput bind:value={search} />
	</div>
	<button id="after-search">After search</button>
	<Tooltip content="Helpful tooltip">
		<button id="tooltip-trigger">Tooltip trigger</button>
	</Tooltip>
	<div id="features-menu">
		<InputMenu
			screenCaptureHandler={noop}
			uploadFilesHandler={noop}
			uploadGoogleDriveHandler={noop}
			onClose={noop}
			webSearchEnabled={false}
			wikiGroundingEnabled={false}
			imageGenerationEnabled={false}
		>
			Open features
		</InputMenu>
	</div>
	<button id="show-shortcuts" on:click={() => (showShortcuts = true)}>Shortcuts</button>
	<ShortcutsModal bind:show={showShortcuts} />
	<div id="single-model">
		<ChatPlaceholder modelIds={['test-model']} submitPrompt={noop} />
	</div>
	<div id="multiple-models">
		<ChatPlaceholder modelIds={['test-model', 'other-model']} submitPrompt={noop} />
	</div>
	<div id="welcome-single">
		<Placeholder
			{history}
			selectedModels={['test-model']}
			createMessagePair={noop}
			stopResponse={noop}
		/>
	</div>
{:else if controlsOnly}
	<div id="chat-container" class="h-[600px]">
		<button id="open-controls" on:click={() => showControls.set(true)}>Open controls</button>
		<PaneGroup direction="horizontal">
			<Pane defaultSize={50}><button id="outside-controls">Outside controls</button></Pane>
			<ChatControls
				{history}
				chatId="test-chat"
				models={[model]}
				eventTarget={new EventTarget()}
				submitPrompt={noop}
				stopResponse={noop}
				showMessage={noop}
				files={[]}
				modelId="test-model"
			/>
		</PaneGroup>
		<button id="after-controls">After controls</button>
	</div>
{:else if touchControls}
	<div id="touch-controls" class="p-8 space-y-8">
		<Folder id="test-folder" name="Test folder" onAddLabel="Add item" onAdd={noop} />
		<Markdown id="touch-table" content={'| First | Second |\n| --- | --- |\n| A | B |'} />
		<WebSearchResults status={{ query: 'Test search', urls: [] }}>Search results</WebSearchResults>
		<MessageInput
			{history}
			selectedModels={['test-model']}
			createMessagePair={noop}
			stopResponse={noop}
			files={[{ type: 'image', url: '/static/favicon.png' }]}
		/>
	</div>
{:else}
	<button id="before">Before</button>
	<div>
		{#each ['first', 'response', 'last'] as messageId, idx}
			<Message
				{idx}
				{messageId}
				bind:history
				chatId="test-chat"
				user={{ name: 'Alex' }}
				showPreviousMessage={noop}
				showNextMessage={noop}
				updateChat={noop}
				editMessage={noop}
				saveMessage={noop}
				deleteMessage={noop}
				rateMessage={noop}
				actionMessage={noop}
				submitMessage={noop}
				regenerateResponse={noop}
				continueResponse={noop}
				mergeResponses={noop}
				addMessages={noop}
				triggerScroll={noop}
			/>
		{/each}
	</div>
	<button id="remove-attachment">Remove</button>
	<textarea
		id="chat-input"
		aria-label="Composer"
		on:keydown={(e) => {
			if (isJumpToMessagesShortcut(e) && focusActiveMessage()) e.preventDefault();
		}}
	></textarea>
	<Selector
		id="test"
		value="test-model"
		showTemporaryChatControl
		items={[{ label: 'Test model', value: 'test-model', model }]}
	/>
	<RecursiveFolder
		folderId="folder-a"
		folders={{
			'folder-a': { id: 'folder-a', name: 'Folder A', is_expanded: false, items: { chats: [] } }
		}}
	/>
	<ChatMenu
		chatId="test-chat"
		buttonID="test-chat-menu"
		ariaLabel="Chat Menu"
		cloneChatHandler={noop}
		archiveChatHandler={noop}
		renameHandler={noop}
		deleteHandler={noop}
		onClose={noop}
		on:change={() => changed++}
	>
		Chat Menu
	</ChatMenu>
	<output aria-label="Changes">{changed}</output>
	<output aria-label="Announcement">{$ariaMessage}</output>
	<ChatItem id="keyboard-chat" title="Keyboard chat" />
	<Collapsible title="Advanced Params">
		<div slot="content">
			<button class="outline-none">Default</button>
		</div>
	</Collapsible>
	<button id="after">After</button>
{/if}
