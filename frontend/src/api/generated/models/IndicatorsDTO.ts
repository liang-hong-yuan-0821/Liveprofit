/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BollBandsDTO } from './BollBandsDTO';
import type { MacdDTO } from './MacdDTO';
import type { MaLineDTO } from './MaLineDTO';
/**
 * MA/BOLL/MACD 技术指标（值取自 idx_factor_pro 入库数据，不自算）。
 *
 * bars 为空时整个字段为 null；macd 可选——旧后端响应无此字段不破坏解析。
 */
export type IndicatorsDTO = {
    ma: Array<MaLineDTO>;
    boll: BollBandsDTO;
    macd?: (MacdDTO | null);
};

