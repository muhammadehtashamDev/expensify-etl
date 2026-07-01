<#macro toJson x><#if x?is_number>${x?c}<#elseif x?is_boolean>${x?string("true","false")}<#elseif x?is_sequence>[<#list x as item><@toJson item/><#sep>,</#sep></#list>]<#elseif x?is_hash_ex>{<#list x?keys as k>"${k?json_string}":<@toJson (x[k])!/><#sep>,</#sep></#list>}<#elseif x?is_string>"${x?json_string}"<#else>null</#if></#macro>
<#if addHeader == true>"report_id","old_report_id","report_name","action_data"
</#if><#list reports as report><#list (report.actionList![]) as act><#assign d><@toJson act/></#assign>"${(report.reportID!"")}","${(report.oldReportID!"")}","${(report.reportName!"")?replace('"','""')}","${d?replace('"','""')}"
</#list></#list>
