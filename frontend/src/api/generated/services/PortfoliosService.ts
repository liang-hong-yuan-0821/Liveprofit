/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Envelope_DeleteResultData_ } from '../models/Envelope_DeleteResultData_';
import type { Envelope_PortfolioDTO_ } from '../models/Envelope_PortfolioDTO_';
import type { Envelope_PortfolioListData_ } from '../models/Envelope_PortfolioListData_';
import type { Envelope_PortfolioPositionMutationData_ } from '../models/Envelope_PortfolioPositionMutationData_';
import type { Envelope_PortfolioPositionsData_ } from '../models/Envelope_PortfolioPositionsData_';
import type { PortfolioCreateRequest } from '../models/PortfolioCreateRequest';
import type { PortfolioUpdateRequest } from '../models/PortfolioUpdateRequest';
import type { PositionUpsertRequest } from '../models/PositionUpsertRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class PortfoliosService {
    /**
     * List Portfolios
     * @param cursor
     * @param limit
     * @returns Envelope_PortfolioListData_ Successful Response
     * @throws ApiError
     */
    public static listPortfoliosApiV1PortfoliosGet(
        cursor?: (string | null),
        limit: number = 20,
    ): CancelablePromise<Envelope_PortfolioListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/portfolios',
            query: {
                'cursor': cursor,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Portfolio
     * @param requestBody
     * @returns Envelope_PortfolioDTO_ Successful Response
     * @throws ApiError
     */
    public static createPortfolioApiV1PortfoliosPost(
        requestBody: PortfolioCreateRequest,
    ): CancelablePromise<Envelope_PortfolioDTO_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/portfolios',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Rename Portfolio
     * @param portfolioId
     * @param requestBody
     * @returns Envelope_PortfolioDTO_ Successful Response
     * @throws ApiError
     */
    public static renamePortfolioApiV1PortfoliosPortfolioIdPatch(
        portfolioId: string,
        requestBody: PortfolioUpdateRequest,
    ): CancelablePromise<Envelope_PortfolioDTO_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/portfolios/{portfolio_id}',
            path: {
                'portfolio_id': portfolioId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Portfolio
     * @param portfolioId
     * @param expectedVersion
     * @returns Envelope_DeleteResultData_ Successful Response
     * @throws ApiError
     */
    public static deletePortfolioApiV1PortfoliosPortfolioIdDelete(
        portfolioId: string,
        expectedVersion: number,
    ): CancelablePromise<Envelope_DeleteResultData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/v1/portfolios/{portfolio_id}',
            path: {
                'portfolio_id': portfolioId,
            },
            query: {
                'expected_version': expectedVersion,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Positions
     * @param portfolioId
     * @returns Envelope_PortfolioPositionsData_ Successful Response
     * @throws ApiError
     */
    public static listPositionsApiV1PortfoliosPortfolioIdPositionsGet(
        portfolioId: string,
    ): CancelablePromise<Envelope_PortfolioPositionsData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/portfolios/{portfolio_id}/positions',
            path: {
                'portfolio_id': portfolioId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Upsert Position
     * @param portfolioId
     * @param market
     * @param symbol
     * @param requestBody
     * @returns Envelope_PortfolioPositionMutationData_ Successful Response
     * @throws ApiError
     */
    public static upsertPositionApiV1PortfoliosPortfolioIdPositionsMarketSymbolPut(
        portfolioId: string,
        market: string,
        symbol: string,
        requestBody: PositionUpsertRequest,
    ): CancelablePromise<Envelope_PortfolioPositionMutationData_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/v1/portfolios/{portfolio_id}/positions/{market}/{symbol}',
            path: {
                'portfolio_id': portfolioId,
                'market': market,
                'symbol': symbol,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Remove Position
     * @param portfolioId
     * @param market
     * @param symbol
     * @param expectedPortfolioRevision
     * @returns Envelope_DeleteResultData_ Successful Response
     * @throws ApiError
     */
    public static removePositionApiV1PortfoliosPortfolioIdPositionsMarketSymbolDelete(
        portfolioId: string,
        market: string,
        symbol: string,
        expectedPortfolioRevision: number,
    ): CancelablePromise<Envelope_DeleteResultData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/v1/portfolios/{portfolio_id}/positions/{market}/{symbol}',
            path: {
                'portfolio_id': portfolioId,
                'market': market,
                'symbol': symbol,
            },
            query: {
                'expected_portfolio_revision': expectedPortfolioRevision,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
