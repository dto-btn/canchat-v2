<script lang="ts">
	import { getI18n } from '$lib/utils/context';

	import { toast } from 'svelte-sonner';

	import dayjs from 'dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	import isToday from 'dayjs/plugin/isToday';
	import isYesterday from 'dayjs/plugin/isYesterday';

	dayjs.extend(relativeTime);
	dayjs.extend(isToday);
	dayjs.extend(isYesterday);
	import { tick } from 'svelte';

	import { settings, user } from '$lib/stores';

	import Message from './Messages/Message.svelte';
	import Loader from '../common/Loader.svelte';
	import Spinner from '../common/Spinner.svelte';
	import { addReaction, deleteMessage, removeReaction, updateMessage } from '$lib/apis/channels';
	import { getRequestToken } from '$lib/services/auth';

	const i18n = getI18n();

	export let id: any = null;
	export let channel: any = null;
	export let messages: any[] = [];
	export let top = false;
	export let thread = false;

	export let onLoad: Function = () => {};
	export let onThread: Function = () => {};

	let messagesLoading = false;

	const loadMoreMessages = async () => {
		// scroll slightly down to disable continuous loading
		const element = document.getElementById('messages-container');
		element.scrollTop = element.scrollTop + 100;

		messagesLoading = true;

		await onLoad();

		await tick();
		messagesLoading = false;
	};

	const deleteChannelMessage = async (message: any) => {
		messages = messages.filter((m: any) => m.id !== message.id);

		await deleteMessage(getRequestToken(), message.channel_id, message.id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
	};

	const editChannelMessage = async (message: any, content: any) => {
		messages = messages.map((m: any) => {
			if (m.id === message.id) {
				m.content = content;
			}
			return m;
		});

		await updateMessage(getRequestToken(), message.channel_id, message.id, {
			content
		}).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
	};

	const openThread = (threadId: any) => {
		onThread(threadId);
	};

	const getEditHandler = (message: any) => {
		return (content: any) => editChannelMessage(message, content);
	};

	const getReactionHandler = (message: any) => {
		return (name: any) => toggleReaction(message, name);
	};

	const toggleReaction = async (message: any, name: any) => {
		if (
			(message?.reactions ?? [])
				.find((reaction: any) => reaction.name === name)
				?.user_ids?.includes($user.id) ??
			false
		) {
			messages = messages.map((m: any) => {
				if (m.id === message.id) {
					const reaction = m.reactions.find((reaction: any) => reaction.name === name);

					if (reaction) {
						reaction.user_ids = reaction.user_ids.filter((id: any) => id !== $user.id);
						reaction.count = reaction.user_ids.length;

						if (reaction.count === 0) {
							m.reactions = m.reactions.filter((r: any) => r.name !== name);
						}
					}
				}
				return m;
			});

			await removeReaction(getRequestToken(), message.channel_id, message.id, name).catch(
				(error) => {
					toast.error(`${error}`);
					return null;
				}
			);
		} else {
			messages = messages.map((m: any) => {
				if (m.id === message.id) {
					if (m.reactions) {
						const reaction = m.reactions.find((reaction: any) => reaction.name === name);

						if (reaction) {
							reaction.user_ids.push($user.id);
							reaction.count = reaction.user_ids.length;
						} else {
							m.reactions.push({
								name,
								user_ids: [$user.id],
								count: 1
							});
						}
					}
				}
				return m;
			});

			await addReaction(getRequestToken(), message.channel_id, message.id, name).catch((error) => {
				toast.error(`${error}`);
				return null;
			});
		}
	};
</script>

{#if messages}
	{@const messageList = messages.slice().reverse()}
	<div>
		{#if !top}
			<Loader
				on:visible={(e) => {
					if (!messagesLoading) {
						loadMoreMessages();
					}
				}}
			>
				<div class="w-full flex justify-center py-1 text-xs animate-pulse items-center gap-2">
					<Spinner className=" size-4" />
					<div class=" ">Loading...</div>
				</div>
			</Loader>
		{:else if !thread}
			<div
				class="px-5
			
			{($settings?.widescreenMode ?? null) ? 'max-w-full' : 'max-w-5xl'} mx-auto"
			>
				{#if channel}
					<div class="flex flex-col gap-1.5 pb-5 pt-10">
						<div class="text-2xl font-medium capitalize">{channel.name}</div>

						<div class=" text-gray-500">
							This channel was created on {dayjs(channel.created_at / 1000000).format(
								'MMMM D, YYYY'
							)}. This is the very beginning of the {channel.name}
							channel.
						</div>
					</div>
				{:else}
					<div class="flex justify-center text-xs items-center gap-2 py-5">
						<div class=" ">Start of the channel</div>
					</div>
				{/if}

				{#if messageList.length > 0}
					<hr class=" border-gray-50 dark:border-gray-700/20 py-2.5 w-full" />
				{/if}
			</div>
		{/if}

		{#each messageList as message, messageIdx (id ? `${id}-${message.id}` : message.id)}
			<Message
				{message}
				{thread}
				showUserProfile={messageIdx === 0 ||
					messageList.at(messageIdx - 1)?.user_id !== message.user_id}
				onDelete={() => deleteChannelMessage(message)}
				onEdit={getEditHandler(message)}
				onThread={openThread}
				onReaction={getReactionHandler(message)}
			/>
		{/each}

		<div class="pb-6" />
	</div>
{/if}
