declare module "argon2-browser" {
  export enum ArgonType {
    Argon2d = 0,
    Argon2i = 1,
    Argon2id = 2,
  }

  export interface HashResult {
    hash: Uint8Array;
    hashHex: string;
    encoded: string;
  }

  export interface HashOptions {
    pass: string | Uint8Array;
    salt: string | Uint8Array;
    time?: number;
    mem?: number;
    hashLen?: number;
    parallelism?: number;
    type?: ArgonType;
  }

  export function hash(options: HashOptions): Promise<HashResult>;
  export function verify(options: {
    pass: string | Uint8Array;
    encoded: string;
    type?: ArgonType;
  }): Promise<boolean>;

  const argon2: {
    ArgonType: typeof ArgonType;
    hash: typeof hash;
    verify: typeof verify;
  };

  export default argon2;
}
