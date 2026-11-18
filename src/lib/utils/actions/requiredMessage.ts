import type { ActionReturn } from 'svelte/action';

type RequiredField = HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;

/**
 * Replaces the browser's native "value missing" message on a `required` field with a
 * translated one, e.g. `<input required use:requiredMessage={() => $i18n.t('This field is required')} />`.
 *
 * `setCustomValidity()` keeps a field invalid until it is reset to '', so the message is
 * cleared whenever the user edits the field; otherwise the form could never be resubmitted.
 */
export function requiredMessage(
	node: RequiredField,
	getMessage: () => string
): ActionReturn<() => string> {
	const onInvalid = () => {
		// Only override "required" failures; other failures (type, pattern, ...) keep the
		// browser's message. Resetting here also unsticks a field whose value was changed
		// programmatically, since that fires no input event.
		node.setCustomValidity(node.validity.valueMissing ? getMessage() : '');
	};
	const onEdit = () => node.setCustomValidity('');

	node.addEventListener('invalid', onInvalid);
	node.addEventListener('input', onEdit);
	node.addEventListener('change', onEdit);

	return {
		update(next) {
			getMessage = next;
		},
		destroy() {
			node.removeEventListener('invalid', onInvalid);
			node.removeEventListener('input', onEdit);
			node.removeEventListener('change', onEdit);
		}
	};
}
