/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type PredictionRequest = {
    event_text: string;
    asset_ticker: string;
    window_type?: PredictionRequest.window_type;
    event_type?: (string | null);
    event_subtype?: (string | null);
    event_condition?: (string | null);
    save?: boolean;
    event_id?: (number | null);
};
export namespace PredictionRequest {
    export enum window_type {
        PRE_EVENT_5D = 'pre_event_5d',
        EVENT_DAY = 'event_day',
        POST_EVENT_5D = 'post_event_5d',
    }
}

