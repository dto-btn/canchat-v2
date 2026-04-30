import { getContext } from 'svelte';
import type { Writable } from 'svelte/store';
import type { i18n as i18nType } from 'i18next';

export function getI18n(): Writable<i18nType> {
	return getContext('i18n');
}
