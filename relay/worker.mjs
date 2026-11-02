import { connect } from 'cloudflare:sockets';
import { createHandler } from './handler.mjs';

export default createHandler(connect);
