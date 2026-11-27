<script lang="ts">
	import { getI18n } from '$lib/utils/context';

	const i18n = getI18n();

	import { WEBUI_BASE_URL } from '$lib/constants';
	import ImagePreview from './ImagePreview.svelte';

	export let src = '';
	export let alt = '';

	export let className = ' w-full outline-none';
	export let imageClassName = 'rounded-lg';

	let _src = '';
	$: _src = src.startsWith('/') ? `${WEBUI_BASE_URL}${src}` : src;

	let showImagePreview = false;
</script>

<button
	aria-label={alt || $i18n.t('Open image')}
	class={className}
	on:click={() => {
		showImagePreview = true;
	}}
	type="button"
>
	<img src={_src} {alt} class={imageClassName} draggable="false" data-cy="image" />
</button>

<ImagePreview bind:show={showImagePreview} src={_src} {alt} />
