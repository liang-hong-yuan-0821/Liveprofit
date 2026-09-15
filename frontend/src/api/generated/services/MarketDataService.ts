/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Envelope_BarsData_ } from '../models/Envelope_BarsData_';
import type { Envelope_ConceptTreeData_ } from '../models/Envelope_ConceptTreeData_';
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
     * Stock Bars
     * 个股 K 线（板块概念Treemap方案 3.3）：复用 get_bars（instrument_type 放宽 stock）。
     * @param symbol
     * @param market
     * @param interval
     * @param from
     * @param to
     * @returns Envelope_BarsData_ Successful Response
     * @throws ApiError
     */
    public static stockBarsApiV1MarketDataStocksSymbolBarsGet(
        symbol: string,
        market: string,
        interval: string,
        from: string,
        to: string,
    ): CancelablePromise<Envelope_BarsData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/market-data/stocks/{symbol}/bars',
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
     * Sector Bars
     * 概念 K 线（板块概念Treemap方案 3.3）：读 market.sector_daily（source 缺省 'dc'）。
     * @param sectorCode
     * @param market
     * @param interval
     * @param from
     * @param to
     * @param source
     * @returns Envelope_BarsData_ Successful Response
     * @throws ApiError
     */
    public static sectorBarsApiV1MarketDataConceptsSectorCodeBarsGet(
        sectorCode: string,
        market: string,
        interval: string,
        from: string,
        to: string,
        source: string = 'dc',
    ): CancelablePromise<Envelope_BarsData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/market-data/concepts/{sector_code}/bars',
            path: {
                'sector_code': sectorCode,
            },
            query: {
                'market': market,
                'source': source,
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
     * Concept Tree
     * 概念树（板块概念Treemap方案 3.2）：热度 top N + 当日涨跌幅 + 成分股。
     *
     * from_ 作为 as_of 透传、to 忽略（m6 定稿同款口径）；limit 必填（同 hot 端点）。
     * @param market
     * @param interval
     * @param from
     * @param to
     * @param limit
     * @param asOf
     * @returns Envelope_ConceptTreeData_ Successful Response
     * @throws ApiError
     */
    public static conceptTreeApiV1MarketDataConceptsTreeGet(
        market: string,
        interval: string,
        from: string,
        to: string,
        limit: number,
        asOf?: (string | null),
    ): CancelablePromise<Envelope_ConceptTreeData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/market-data/concepts/tree',
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
