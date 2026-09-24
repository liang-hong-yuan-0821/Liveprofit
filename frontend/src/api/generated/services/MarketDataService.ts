/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Envelope_BarsData_ } from '../models/Envelope_BarsData_';
import type { Envelope_ConceptTreeData_ } from '../models/Envelope_ConceptTreeData_';
import type { Envelope_HotConceptsData_ } from '../models/Envelope_HotConceptsData_';
import type { Envelope_RefreshDecisionsData_ } from '../models/Envelope_RefreshDecisionsData_';
import type { Envelope_RefreshJob_ } from '../models/Envelope_RefreshJob_';
import type { Envelope_RefreshStatusData_ } from '../models/Envelope_RefreshStatusData_';
import type { Envelope_TrendsData_ } from '../models/Envelope_TrendsData_';
import type { RefreshRequest } from '../models/RefreshRequest';
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
     * @param factorPolicy
     * @returns Envelope_BarsData_ Successful Response
     * @throws ApiError
     */
    public static stockBarsApiV1MarketDataStocksSymbolBarsGet(
        symbol: string,
        market: string,
        interval: string,
        from: string,
        to: string,
        factorPolicy: 'ensure' | 'cache_only' = 'ensure',
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
                'factor_policy': factorPolicy,
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
     * Latest actual sector date by default; explicit as_of reads exactly that day.
     * @param market
     * @param interval
     * @param limit
     * @param asOf
     * @returns Envelope_ConceptTreeData_ Successful Response
     * @throws ApiError
     */
    public static conceptTreeApiV1MarketDataConceptsTreeGet(
        market: string,
        interval: string,
        limit: number,
        asOf?: (string | null),
    ): CancelablePromise<Envelope_ConceptTreeData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/market-data/concepts/tree',
            query: {
                'market': market,
                'interval': interval,
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
     * @param limit
     * @param asOf
     * @returns Envelope_HotConceptsData_ Successful Response
     * @throws ApiError
     */
    public static hotConceptsApiV1MarketDataConceptsHotGet(
        market: string,
        interval: string,
        limit: number,
        asOf?: (string | null),
    ): CancelablePromise<Envelope_HotConceptsData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/market-data/concepts/hot',
            query: {
                'market': market,
                'interval': interval,
                'limit': limit,
                'as_of': asOf,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Cap Tier Trends
     * 市值分层趋势（趋势对比面板方案 4.2）：沪深300/中证500/中证1000/中证2000
     * 四序列，前端按共同首日=100 归一。
     * @param from
     * @param to
     * @returns Envelope_TrendsData_ Successful Response
     * @throws ApiError
     */
    public static capTierTrendsApiV1MarketDataTrendsCapTiersGet(
        from: string,
        to: string,
    ): CancelablePromise<Envelope_TrendsData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/market-data/trends/cap-tiers',
            query: {
                'from': from,
                'to': to,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Board Trends
     * 市场板趋势（趋势对比面板方案 4.2）：上证综指/创业板指/科创50 官方指数
     * 三序列，前端按共同首日=100 归一。
     * @param from
     * @param to
     * @returns Envelope_TrendsData_ Successful Response
     * @throws ApiError
     */
    public static boardTrendsApiV1MarketDataTrendsBoardsGet(
        from: string,
        to: string,
    ): CancelablePromise<Envelope_TrendsData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/market-data/trends/boards',
            query: {
                'from': from,
                'to': to,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Refresh Status
     * @returns Envelope_RefreshStatusData_ Successful Response
     * @throws ApiError
     */
    public static refreshStatusApiV1MarketDataRefreshStatusGet(): CancelablePromise<Envelope_RefreshStatusData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/market-data/refresh-status',
        });
    }
    /**
     * Refresh
     * @param requestBody
     * @returns Envelope_RefreshDecisionsData_ Successful Response
     * @throws ApiError
     */
    public static refreshApiV1MarketDataRefreshPost(
        requestBody: RefreshRequest,
    ): CancelablePromise<Envelope_RefreshDecisionsData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/market-data/refresh',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Refresh Job
     * @param jobId
     * @returns Envelope_RefreshJob_ Successful Response
     * @throws ApiError
     */
    public static refreshJobApiV1MarketDataRefreshJobsJobIdGet(
        jobId: string,
    ): CancelablePromise<Envelope_RefreshJob_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/market-data/refresh-jobs/{job_id}',
            path: {
                'job_id': jobId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
