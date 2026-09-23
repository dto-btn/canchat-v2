<script lang="ts">
	import { Handle, Position, type NodeProps } from '@xyflow/svelte';

	import ProfileImage from '../Messages/ProfileImage.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Heart from '$lib/components/icons/Heart.svelte';

	type NodeData = {
		message: {
			role: string;
			content: string;
			error?: { content?: string };
			favorite?: boolean;
			model?: string;
		};
		user?: { profile_image_url?: string; name?: string };
		model?: { name?: string; info?: { meta?: { profile_image_url?: string } } };
	};

	type $$Props = NodeProps;
	export let data: NodeData | Record<string, unknown>;

	$: nodeData = data as NodeData;
	const toggleFavorite = () => {
		nodeData.message.favorite = !nodeData.message.favorite;
	};
</script>

<div
	class="px-4 py-3 shadow-md rounded-xl dark:bg-black bg-white border dark:border-gray-900 w-60 h-20 group"
>
	<Tooltip
		content={nodeData?.message?.error ? nodeData.message.error.content : nodeData.message.content}
		class="w-full"
		allowHTML={false}
	>
		{#if nodeData.message.role === 'user'}
			<div class="flex w-full">
				<ProfileImage
					src={nodeData.user?.profile_image_url ?? '/user.png'}
					className={'size-5 -translate-y-[1px]'}
				/>
				<div class="ml-2">
					<div class=" flex justify-between items-center">
						<div class="text-xs text-black dark:text-white font-medium line-clamp-1">
							{nodeData?.user?.name ?? 'User'}
						</div>
					</div>

					{#if nodeData?.message?.error}
						<div class="text-red-500 line-clamp-2 text-xs mt-0.5">
							{nodeData.message.error.content}
						</div>
					{:else}
						<div class="text-gray-500 line-clamp-2 text-xs mt-0.5">{nodeData.message.content}</div>
					{/if}
				</div>
			</div>
		{:else}
			<div class="flex w-full">
				<ProfileImage
					src={nodeData?.model?.info?.meta?.profile_image_url ?? ''}
					className={'size-5 -translate-y-[1px]'}
				/>

				<div class="ml-2">
					<div class=" flex justify-between items-center">
						<div class="text-xs text-black dark:text-white font-medium line-clamp-1">
							{nodeData?.model?.name ?? nodeData?.message?.model ?? 'Assistant'}
						</div>

						<button
							class={nodeData?.message?.favorite ? '' : 'invisible group-hover:visible'}
							on:click={() => {
								toggleFavorite();
							}}
						>
							<Heart
								className="size-3 {nodeData?.message?.favorite
									? 'fill-red-500 stroke-red-500'
									: 'hover:fill-red-500 hover:stroke-red-500'} "
								strokeWidth="2.5"
							/>
						</button>
					</div>

					{#if nodeData?.message?.error}
						<div class="text-red-500 line-clamp-2 text-xs mt-0.5">
							{nodeData.message.error.content}
						</div>
					{:else}
						<div class="text-gray-500 line-clamp-2 text-xs mt-0.5">{nodeData.message.content}</div>
					{/if}
				</div>
			</div>
		{/if}
	</Tooltip>
	<Handle type="target" position={Position.Top} class="w-2 rounded-full dark:bg-gray-900" />
	<Handle type="source" position={Position.Bottom} class="w-2 rounded-full dark:bg-gray-900" />
</div>
