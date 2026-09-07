/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Envelope_DeleteResultData_ } from '../models/Envelope_DeleteResultData_';
import type { Envelope_WatchlistDTO_ } from '../models/Envelope_WatchlistDTO_';
import type { Envelope_WatchlistItemMutationData_ } from '../models/Envelope_WatchlistItemMutationData_';
import type { Envelope_WatchlistItemsData_ } from '../models/Envelope_WatchlistItemsData_';
import type { Envelope_WatchlistListData_ } from '../models/Envelope_WatchlistListData_';
import type { Envelope_WatchlistOrderData_ } from '../models/Envelope_WatchlistOrderData_';
import type { WatchlistCreateRequest } from '../models/WatchlistCreateRequest';
import type { WatchlistItemCreateRequest } from '../models/WatchlistItemCreateRequest';
import type { WatchlistItemOrderUpdateRequest } from '../models/WatchlistItemOrderUpdateRequest';
import type { WatchlistUpdateRequest } from '../models/WatchlistUpdateRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class WatchlistsService {
    /**
     * List Watchlists
     * @param cursor
     * @param limit
     * @returns Envelope_WatchlistListData_ Successful Response
     * @throws ApiError
     */
    public static listWatchlistsApiV1WatchlistsGet(
        cursor?: (string | null),
        limit: number = 20,
    ): CancelablePromise<Envelope_WatchlistListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/watchlists',
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
     * Create Watchlist
     * @param requestBody
     * @returns Envelope_WatchlistDTO_ Successful Response
     * @throws ApiError
     */
    public static createWatchlistApiV1WatchlistsPost(
        requestBody: WatchlistCreateRequest,
    ): CancelablePromise<Envelope_WatchlistDTO_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/watchlists',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Rename Watchlist
     * @param watchlistId
     * @param requestBody
     * @returns Envelope_WatchlistDTO_ Successful Response
     * @throws ApiError
     */
    public static renameWatchlistApiV1WatchlistsWatchlistIdPatch(
        watchlistId: string,
        requestBody: WatchlistUpdateRequest,
    ): CancelablePromise<Envelope_WatchlistDTO_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/watchlists/{watchlist_id}',
            path: {
                'watchlist_id': watchlistId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Watchlist
     * @param watchlistId
     * @param expectedVersion
     * @returns Envelope_DeleteResultData_ Successful Response
     * @throws ApiError
     */
    public static deleteWatchlistApiV1WatchlistsWatchlistIdDelete(
        watchlistId: string,
        expectedVersion: number,
    ): CancelablePromise<Envelope_DeleteResultData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/v1/watchlists/{watchlist_id}',
            path: {
                'watchlist_id': watchlistId,
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
     * List Watchlist Items
     * @param watchlistId
     * @returns Envelope_WatchlistItemsData_ Successful Response
     * @throws ApiError
     */
    public static listWatchlistItemsApiV1WatchlistsWatchlistIdItemsGet(
        watchlistId: string,
    ): CancelablePromise<Envelope_WatchlistItemsData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/watchlists/{watchlist_id}/items',
            path: {
                'watchlist_id': watchlistId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Add Watchlist Item
     * @param watchlistId
     * @param requestBody
     * @returns Envelope_WatchlistItemMutationData_ Successful Response
     * @throws ApiError
     */
    public static addWatchlistItemApiV1WatchlistsWatchlistIdItemsPost(
        watchlistId: string,
        requestBody: WatchlistItemCreateRequest,
    ): CancelablePromise<Envelope_WatchlistItemMutationData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/watchlists/{watchlist_id}/items',
            path: {
                'watchlist_id': watchlistId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Reorder Watchlist Items
     * @param watchlistId
     * @param requestBody
     * @returns Envelope_WatchlistOrderData_ Successful Response
     * @throws ApiError
     */
    public static reorderWatchlistItemsApiV1WatchlistsWatchlistIdItemsOrderPut(
        watchlistId: string,
        requestBody: WatchlistItemOrderUpdateRequest,
    ): CancelablePromise<Envelope_WatchlistOrderData_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/v1/watchlists/{watchlist_id}/items/order',
            path: {
                'watchlist_id': watchlistId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Remove Watchlist Item
     * @param watchlistId
     * @param itemId
     * @param expectedWatchlistRevision
     * @returns Envelope_DeleteResultData_ Successful Response
     * @throws ApiError
     */
    public static removeWatchlistItemApiV1WatchlistsWatchlistIdItemsItemIdDelete(
        watchlistId: string,
        itemId: string,
        expectedWatchlistRevision: number,
    ): CancelablePromise<Envelope_DeleteResultData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/v1/watchlists/{watchlist_id}/items/{item_id}',
            path: {
                'watchlist_id': watchlistId,
                'item_id': itemId,
            },
            query: {
                'expected_watchlist_revision': expectedWatchlistRevision,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
