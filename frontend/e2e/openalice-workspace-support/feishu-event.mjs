import { createCipheriv, createHash, randomBytes } from 'node:crypto'

// Actual mounted callbacks and the pinned SDK verify these local dummy events.
export function signedEvent(encryptKey, payload) {
  const iv = randomBytes(16)
  const key = createHash('sha256').update(encryptKey).digest()
  const cipher = createCipheriv('aes-256-cbc', key, iv)
  const encrypted = Buffer.concat([iv, cipher.update(JSON.stringify(payload), 'utf8'), cipher.final()])
  const body = JSON.stringify({ encrypt: encrypted.toString('base64') })
  const timestamp = Math.floor(Date.now() / 1000).toString()
  const nonce = randomBytes(12).toString('hex')
  const signature = createHash('sha256').update(timestamp + nonce + encryptKey + body).digest('hex')
  return {
    data: body,
    headers: {
      'Content-Type': 'application/json',
      'x-lark-request-timestamp': timestamp,
      'x-lark-request-nonce': nonce,
      'x-lark-signature': signature,
    },
  }
}
