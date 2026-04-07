# `qitos.kit.tool.web`

- Module Group: `qitos.kit`
- Source: [qitos/kit/tool/web.py](https://github.com/Qitor/qitos/blob/main/qitos/kit/tool/web.py)

## Quick Jump

- [Classes](#classes)
- [Functions](#functions)
- [Class: `HTMLExtractText`](#class-htmlextracttext)
- [Class: `HTTPGet`](#class-httpget)
- [Class: `HTTPPost`](#class-httppost)
- [Class: `HTTPRequest`](#class-httprequest)
- [Class: `WebFetch`](#class-webfetch)

## Classes

<a id="class-htmlextracttext"></a>
???+ note "Class: `HTMLExtractText(self)`"
    Extract readable text snippets from raw HTML.

<a id="class-httpget"></a>
???+ note "Class: `HTTPGet(self, headers: 'Optional[Dict[str, str]]' = None, timeout: 'int' = 30, max_retries: 'int' = 2)`"
    Issue one HTTP GET request and return a structured response payload.

<a id="class-httppost"></a>
???+ note "Class: `HTTPPost(self, headers: 'Optional[Dict[str, str]]' = None, timeout: 'int' = 30, max_retries: 'int' = 2)`"
    Issue one HTTP POST request and return a structured response payload.

<a id="class-httprequest"></a>
???+ note "Class: `HTTPRequest(self, headers: 'Optional[Dict[str, str]]' = None, timeout: 'int' = 30, max_retries: 'int' = 2, backoff_factor: 'float' = 0.4, user_agent: 'str' = 'QitOS-WebTool/1.0')`"
    Generic HTTP request tool with retries, timeout, and structured output.

<a id="class-webfetch"></a>
???+ note "Class: `WebFetch(self, headers: 'Optional[Dict[str, str]]' = None, timeout: 'int' = 30, max_retries: 'int' = 2)`"
    Fetch a web page and optionally return extracted readable text.

## Functions

- _None_

## Source Index

- [qitos/kit/tool/web.py](https://github.com/Qitor/qitos/blob/main/qitos/kit/tool/web.py)