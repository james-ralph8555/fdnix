/* @refresh reload */
import { render } from 'solid-js/web';
import App from './App';
import './index.css';

// Type declaration for build-time constants
declare global {
  const __API_BASE_URL__: string;
}

const root = document.getElementById('root');

if (import.meta.env.DEV && !(root instanceof HTMLElement)) {
  throw new Error(
    'Root element not found. Did you forget to add it to your index.html? Or maybe the id attribute got mispelled?',
  );
}

render(() => <App />, root!);

// Remove the loading class once the app is rendered
document.body.classList.remove('loading');