/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Envelope_FillMutationData_ } from '../models/Envelope_FillMutationData_';
import type { Envelope_LifecyclePolicyListData_ } from '../models/Envelope_LifecyclePolicyListData_';
import type { Envelope_LifecyclePolicyVersionDTO_ } from '../models/Envelope_LifecyclePolicyVersionDTO_';
import type { Envelope_OrderFillListData_ } from '../models/Envelope_OrderFillListData_';
import type { Envelope_PositionDailyFactPageData_ } from '../models/Envelope_PositionDailyFactPageData_';
import type { Envelope_PositionIntentPageData_ } from '../models/Envelope_PositionIntentPageData_';
import type { Envelope_PositionLifecyclePageData_ } from '../models/Envelope_PositionLifecyclePageData_';
import type { Envelope_SuggestedOrderDTO_ } from '../models/Envelope_SuggestedOrderDTO_';
import type { Envelope_SuggestedOrderListData_ } from '../models/Envelope_SuggestedOrderListData_';
import type { FillConfirmRequest } from '../models/FillConfirmRequest';
import type { FillCorrectRequest } from '../models/FillCorrectRequest';
import type { FillVoidRequest } from '../models/FillVoidRequest';
import type { LifecyclePolicyCreateRequest } from '../models/LifecyclePolicyCreateRequest';
import type { OrderStatusRequest } from '../models/OrderStatusRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class PositionLifecycleService {
    /**
     * List Policy Versions
     * @returns Envelope_LifecyclePolicyListData_ Successful Response
     * @throws ApiError
     */
    public static listPolicyVersionsApiV1LifecyclePolicyVersionsGet(): CancelablePromise<Envelope_LifecyclePolicyListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/lifecycle-policy-versions',
        });
    }
    /**
     * Publish Policy Version
     * @param requestBody
     * @returns Envelope_LifecyclePolicyVersionDTO_ Successful Response
     * @throws ApiError
     */
    public static publishPolicyVersionApiV1LifecyclePolicyVersionsPost(
        requestBody: LifecyclePolicyCreateRequest,
    ): CancelablePromise<Envelope_LifecyclePolicyVersionDTO_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/lifecycle-policy-versions',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Orders
     * @param portfolioId
     * @param positionId
     * @param cursor
     * @param limit
     * @returns Envelope_SuggestedOrderListData_ Successful Response
     * @throws ApiError
     */
    public static listOrdersApiV1PortfoliosPortfolioIdSuggestedOrdersGet(
        portfolioId: string,
        positionId?: (string | null),
        cursor?: (string | null),
        limit: number = 50,
    ): CancelablePromise<Envelope_SuggestedOrderListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/portfolios/{portfolio_id}/suggested-orders',
            path: {
                'portfolio_id': portfolioId,
            },
            query: {
                'position_id': positionId,
                'cursor': cursor,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Fills
     * @param orderId
     * @param cursor
     * @param limit
     * @returns Envelope_OrderFillListData_ Successful Response
     * @throws ApiError
     */
    public static listFillsApiV1SuggestedOrdersOrderIdFillsGet(
        orderId: string,
        cursor?: (string | null),
        limit: number = 50,
    ): CancelablePromise<Envelope_OrderFillListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/suggested-orders/{order_id}/fills',
            path: {
                'order_id': orderId,
            },
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
     * Confirm Fill
     * @param orderId
     * @param requestBody
     * @returns Envelope_FillMutationData_ Successful Response
     * @throws ApiError
     */
    public static confirmFillApiV1SuggestedOrdersOrderIdFillsPost(
        orderId: string,
        requestBody: FillConfirmRequest,
    ): CancelablePromise<Envelope_FillMutationData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/suggested-orders/{order_id}/fills',
            path: {
                'order_id': orderId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Position Lifecycles
     * @param portfolioId
     * @param positionId
     * @param cursor
     * @param limit
     * @returns Envelope_PositionLifecyclePageData_ Successful Response
     * @throws ApiError
     */
    public static listPositionLifecyclesApiV1PortfoliosPortfolioIdPositionLifecyclesGet(
        portfolioId: string,
        positionId?: (string | null),
        cursor?: (string | null),
        limit: number = 50,
    ): CancelablePromise<Envelope_PositionLifecyclePageData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/portfolios/{portfolio_id}/position-lifecycles',
            path: {
                'portfolio_id': portfolioId,
            },
            query: {
                'position_id': positionId,
                'cursor': cursor,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Position Intents
     * @param lifecycleId
     * @param cursor
     * @param limit
     * @returns Envelope_PositionIntentPageData_ Successful Response
     * @throws ApiError
     */
    public static listPositionIntentsApiV1PositionLifecyclesLifecycleIdIntentsGet(
        lifecycleId: string,
        cursor?: (string | null),
        limit: number = 50,
    ): CancelablePromise<Envelope_PositionIntentPageData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/position-lifecycles/{lifecycle_id}/intents',
            path: {
                'lifecycle_id': lifecycleId,
            },
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
     * List Position Daily Facts
     * @param lifecycleId
     * @param cursor
     * @param limit
     * @returns Envelope_PositionDailyFactPageData_ Successful Response
     * @throws ApiError
     */
    public static listPositionDailyFactsApiV1PositionLifecyclesLifecycleIdDailyFactsGet(
        lifecycleId: string,
        cursor?: (string | null),
        limit: number = 50,
    ): CancelablePromise<Envelope_PositionDailyFactPageData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/position-lifecycles/{lifecycle_id}/daily-facts',
            path: {
                'lifecycle_id': lifecycleId,
            },
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
     * Correct Fill
     * @param fillId
     * @param requestBody
     * @returns Envelope_FillMutationData_ Successful Response
     * @throws ApiError
     */
    public static correctFillApiV1OrderFillsFillIdCorrectPost(
        fillId: string,
        requestBody: FillCorrectRequest,
    ): CancelablePromise<Envelope_FillMutationData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/order-fills/{fill_id}/correct',
            path: {
                'fill_id': fillId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Void Fill
     * @param fillId
     * @param requestBody
     * @returns Envelope_FillMutationData_ Successful Response
     * @throws ApiError
     */
    public static voidFillApiV1OrderFillsFillIdVoidPost(
        fillId: string,
        requestBody: FillVoidRequest,
    ): CancelablePromise<Envelope_FillMutationData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/order-fills/{fill_id}/void',
            path: {
                'fill_id': fillId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Set Order Status
     * @param orderId
     * @param requestBody
     * @returns Envelope_SuggestedOrderDTO_ Successful Response
     * @throws ApiError
     */
    public static setOrderStatusApiV1SuggestedOrdersOrderIdStatusPatch(
        orderId: string,
        requestBody: OrderStatusRequest,
    ): CancelablePromise<Envelope_SuggestedOrderDTO_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/suggested-orders/{order_id}/status',
            path: {
                'order_id': orderId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
