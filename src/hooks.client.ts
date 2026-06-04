import { installApiFetchInterceptor } from '$lib/apis/client';

export const init = async () => {
	installApiFetchInterceptor();
};