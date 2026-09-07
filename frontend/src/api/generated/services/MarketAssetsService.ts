/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Envelope_MarketAssetsData_ } from '../models/Envelope_MarketAssetsData_';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class MarketAssetsService {
    /**
     * List Market Assets
     * @param enabled
     * @returns Envelope_MarketAssetsData_ Successful Response
     * @throws ApiError
     */
    public static listMarketAssetsApiV1MarketAssetsGet(
        enabled: boolean = true,
    ): CancelablePromise<Envelope_MarketAssetsData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/market-assets',
            query: {
                'enabled': enabled,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
