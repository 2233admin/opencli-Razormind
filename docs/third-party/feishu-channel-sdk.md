# Feishu Channel SDK

OpenCLI Admin uses the official `lark-channel-sdk[fastapi]` package at version
`1.4.0` for strict Feishu webhook signature verification, decryption,
verification-token validation, and typed event decoding.

- Project and distribution: <https://pypi.org/project/lark-channel-sdk/1.4.0/>
- Upstream source: <https://github.com/larksuite/lark-channel-sdk-python>
- License declared by the distribution: MIT AND BSD-3-Clause
- CPython wheel SHA-256: `5466fc5e92b6405795e3cd8ea7ed570a83cab2bc095ac446f786f2a34ee58eb5`

The integration calls the public synchronous
`EventDispatcherHandler.do(RawRequest)` interface. This lets OpenCLI commit its
own durable receipt before acknowledging the callback. The SDK's higher-level
channel handler is intentionally not used because its acknowledgement and
in-memory duplicate handling do not provide that transaction boundary.

No upstream source is vendored. Runtime credentials remain encrypted in the
OpenCLI database and are never included in API responses or logs.
