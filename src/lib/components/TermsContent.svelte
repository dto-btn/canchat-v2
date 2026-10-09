
<script lang="ts">
	import { marked } from 'marked';
	import DOMPurify from 'dompurify';

	export let markdown: string;

	$: html = DOMPurify.sanitize(marked.parse(markdown) as string);
</script>

<article class="terms-markdown">
	{@html html}
</article>

<style>
	:global(.terms-markdown h2) {
		color: #7e22ce;
		font-size: 1.25rem;
		font-weight: 600;
		margin-top: 1.25rem;
		margin-bottom: 0.75rem;
	}

	:global(.terms-markdown > :first-child) {
		margin-top: 0;
	}

	:global(.terms-markdown p) {
		margin-bottom: 0.75rem;
	}

	:global(.terms-markdown ul) {
		list-style-type: disc;
		padding-left: 1.5rem;
		margin-bottom: 1rem;
	}

	:global(.terms-markdown ol) {
		list-style-type: decimal;
		padding-left: 1.5rem;
		margin-bottom: 1rem;
	}

	:global(.terms-markdown li) {
		margin-bottom: 0.5rem;
	}

	:global(.terms-markdown li::marker) {
		color: currentColor;
	}

	:global(.terms-markdown a) {
		text-decoration: underline;
	}

	:global(.dark .terms-markdown h2) {
		color: #c084fc;
	}
</style>
