<script lang="ts">
	import { getI18n } from '$lib/utils/context';

	import { onDestroy, onMount, tick, createEventDispatcher } from 'svelte';
	const i18n = getI18n();
	const dispatch = createEventDispatcher();

	import Markdown from './Markdown.svelte';
	import { chatId, mobile, showArtifacts, showControls } from '$lib/stores';
	import FloatingButtons from '../ContentRenderer/FloatingButtons.svelte';
	import { createMessagesList } from '$lib/utils';

	export let id: any;
	export let content: any;
	export let history: any;
	export let model: any = null;
	export let sources: any = null;

	export let save = false;
	export let floatingButtons = true;

	export let onSourceClick: () => void = () => {};
	export let onAddMessages: (payload: {
		modelId: string;
		parentId: string;
		messages: any[];
	}) => void = () => {};

	let contentContainerElement: any;

	let floatingButtonsElement: any;

	const updateButtonPosition = (event: any) => {
		const buttonsContainerElement = document.getElementById(`floating-buttons-${id}`);
		if (
			!contentContainerElement?.contains(event.target) &&
			!buttonsContainerElement?.contains(event.target)
		) {
			closeFloatingButtons();
			return;
		}

		setTimeout(async () => {
			await tick();

			if (!contentContainerElement?.contains(event.target)) return;

			let selection = window.getSelection();

			if (selection.toString().trim().length > 0) {
				const range = selection.getRangeAt(0);
				const rect = range.getBoundingClientRect();

				const parentRect = contentContainerElement.getBoundingClientRect();

				// Adjust based on parent rect
				const top = rect.bottom - parentRect.top;
				const left = rect.left - parentRect.left;

				if (buttonsContainerElement) {
					buttonsContainerElement.style.display = 'block';

					// Calculate space available on the right
					const spaceOnRight = parentRect.width - left;
					let halfScreenWidth = $mobile ? window.innerWidth / 2 : window.innerWidth / 3;

					if (spaceOnRight < halfScreenWidth) {
						const right = parentRect.right - rect.right;
						buttonsContainerElement.style.right = `${right}px`;
						buttonsContainerElement.style.left = 'auto'; // Reset left
					} else {
						// Enough space, position using 'left'
						buttonsContainerElement.style.left = `${left}px`;
						buttonsContainerElement.style.right = 'auto'; // Reset right
					}
					buttonsContainerElement.style.top = `${top + 5}px`; // +5 to add some spacing
				}
			} else {
				closeFloatingButtons();
			}
		}, 0);
	};

	const closeFloatingButtons = () => {
		const buttonsContainerElement = document.getElementById(`floating-buttons-${id}`);
		if (buttonsContainerElement) {
			buttonsContainerElement.style.display = 'none';
		}

		if (floatingButtonsElement) {
			floatingButtonsElement.closeHandler();
		}
	};

	const keydownHandler = (e: any) => {
		if (e.key === 'Escape') {
			closeFloatingButtons();
		}
	};

	const getSourceIds = (sourceList: any[] = []) => {
		return sourceList.reduce((acc: any[], s: any) => {
			let ids: any[] = [];
			s.document.forEach((document: any, index: any) => {
				const metadata = s.metadata?.[index];
				const id = metadata?.source ?? 'N/A';

				if (metadata?.name) {
					ids.push(metadata.name);
					return ids;
				}

				if (id.startsWith('http://') || id.startsWith('https://')) {
					ids.push(id);
				} else {
					ids.push(s?.source?.name ?? id);
				}

				return ids;
			});

			acc = [...acc, ...ids];
			return acc.filter((item, index) => acc.indexOf(item) === index);
		}, []);
	};

	const handleAddMessages = ({ modelId, parentId, messages }: any) => {
		onAddMessages({ modelId, parentId, messages });
		closeFloatingButtons();
	};

	onMount(() => {
		if (floatingButtons) {
			contentContainerElement?.addEventListener('mouseup', updateButtonPosition);
			document.addEventListener('mouseup', updateButtonPosition);
			document.addEventListener('keydown', keydownHandler);
		}
	});

	onDestroy(() => {
		if (floatingButtons) {
			contentContainerElement?.removeEventListener('mouseup', updateButtonPosition);
			document.removeEventListener('mouseup', updateButtonPosition);
			document.removeEventListener('keydown', keydownHandler);
		}
	});
</script>

<div bind:this={contentContainerElement}>
	<Markdown
		{id}
		{content}
		{model}
		{save}
		sourceIds={getSourceIds(sources ?? [])}
		{onSourceClick}
		on:update={(e) => {
			dispatch('update', e.detail);
		}}
		on:code={(e) => {
			const { lang, code } = e.detail;

			if (
				(['html', 'svg'].includes(lang) || (lang === 'xml' && code.includes('svg'))) &&
				!$mobile &&
				$chatId
			) {
				showArtifacts.set(true);
				showControls.set(true);
			}
		}}
	/>
</div>

{#if floatingButtons && model}
	<FloatingButtons
		bind:this={floatingButtonsElement}
		{id}
		model={model?.id}
		messages={createMessagesList(history, id)}
		onAdd={handleAddMessages}
	/>
{/if}
