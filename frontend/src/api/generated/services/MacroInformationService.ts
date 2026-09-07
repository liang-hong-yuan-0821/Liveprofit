/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Envelope_MacroInformationData_ } from '../models/Envelope_MacroInformationData_';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class MacroInformationService {
    /**
     * List Macro Information
     * @param limit
     * @param cursor
     * @param market
     * @param topic
     * @returns Envelope_MacroInformationData_ Successful Response
     * @throws ApiError
     */
    public static listMacroInformationApiV1MacroInformationGet(
        limit: number,
        cursor?: (string | null),
        market?: (string | null),
        topic?: (string | null),
    ): CancelablePromise<Envelope_MacroInformationData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/macro-information',
            query: {
                'limit': limit,
                'cursor': cursor,
                'market': market,
                'topic': topic,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
