import { renameSync } from 'node:fs';
renameSync(new URL('../../localmedia/static/localmedia.html', import.meta.url), new URL('../../localmedia/static/index.html', import.meta.url));
