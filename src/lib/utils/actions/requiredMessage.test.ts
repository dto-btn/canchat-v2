import { describe, it, expect } from 'vitest';
import { requiredMessage } from './requiredMessage';

// Minimal stand-in for a required <input>: a field stays invalid while it is empty
// OR while a non-empty custom validity message is set, as in the browser.
class FakeField extends EventTarget {
	value = '';
	typeMismatch = false;
	validationMessage = '';
	get validity() {
		return { valueMissing: this.value === '', typeMismatch: this.typeMismatch };
	}
	setCustomValidity(msg: string) {
		this.validationMessage = msg;
	}
	checkValidity() {
		const valid = !this.validity.valueMissing && !this.typeMismatch && !this.validationMessage;
		if (!valid) this.dispatchEvent(new Event('invalid'));
		return valid;
	}
	type(value: string) {
		this.value = value;
		this.dispatchEvent(new Event('input'));
	}
}

const setup = () => {
	const field = new FakeField();
	const action = requiredMessage(field as unknown as HTMLInputElement, () => 'Required');
	return { field, action };
};

describe('requiredMessage', () => {
	it('shows the custom message when the field is empty', () => {
		const { field } = setup();
		expect(field.checkValidity()).toBe(false);
		expect(field.validationMessage).toBe('Required');
	});

	it('lets the form submit again once the user fills the field', () => {
		const { field } = setup();
		field.checkValidity();
		field.type('hello');
		expect(field.checkValidity()).toBe(true);
	});

	it('recovers when the value was set without an input event', () => {
		const { field } = setup();
		field.checkValidity();
		field.value = 'filled programmatically';
		field.checkValidity();
		expect(field.checkValidity()).toBe(true);
	});

	it('does not label non-required failures as required', () => {
		const { field } = setup();
		field.value = 'not-an-email';
		field.typeMismatch = true;
		field.checkValidity();
		expect(field.validationMessage).toBe('');
	});

	it('uses the latest message after update and detaches on destroy', () => {
		const { field, action } = setup();
		action.update?.(() => 'Champ obligatoire');
		field.checkValidity();
		expect(field.validationMessage).toBe('Champ obligatoire');

		action.destroy?.();
		field.type('x');
		expect(field.validationMessage).toBe('Champ obligatoire');
	});
});
