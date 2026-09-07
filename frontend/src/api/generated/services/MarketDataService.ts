/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Envelope_BarsData_ } from '../models/Envelope_BarsData_';
import type { Envelope_HotConceptsData_ } from '../models/Envelope_HotConceptsData_';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class MarketDataService {
    /**
     * Index Bars
     * @param symbol
     * @param market
     * @param interval
     * @param from
     * @param to
     * @returns Envelope_BarsData_ Successful Response
     * @throws ApiError
     */
    public static indexBarsApiV1MarketDataIndicesSymbolBarsGet(
        symbol: string,
        market: string,
        interval: string,
        from: string,
        to: string,
    ): CancelablePromise<Envelope_BarsData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/market-data/indices/{symbol}/bars',
            path: {
                'symbol': symbol,
            },
            query: {
                'market': market,
                'interval': interval,
                'from': from,
                'to': to,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Hot Concepts
     * @param market
     * @param interval
     * @param from
     * @param to
     * @param limit
     * @param asOf
     * @returns Envelope_HotConceptsData_ Successful Response
     * @throws ApiError
     */
    public static hotConceptsApiV1MarketDataConceptsHotGet(
        market: string,
        interval: string,
        from: string,
        to: string,
        limit: number,
        asOf?: (string | null),
    ): CancelablePromise<Envelope_HotConceptsData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/market-data/concepts/hot',
            query: {
                'market': market,
                'interval': interval,
                'from': from,
                'to': to,
                'limit': limit,
                'as_of': asOf,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
