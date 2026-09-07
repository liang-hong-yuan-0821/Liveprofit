/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BollBandsDTO } from './BollBandsDTO';
import type { MaLineDTO } from './MaLineDTO';
/**
 * MA/BOLL 技术指标（K线指标叠加方案 §2.1）；bars 为空时整个字段为 null。
 */
export type IndicatorsDTO = {
    ma: Array<MaLineDTO>;
    boll: BollBandsDTO;
};

