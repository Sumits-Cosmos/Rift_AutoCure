import Navbar from './components/Navbar/Navbar';
import HomePage from './pages/HomePage/HomePage';
import TestingPage from './pages/TestingPage/TestingPage';
import FeaturesPage from './pages/FeaturesPage/FeaturesPage';
import DocsPage from './pages/DocsPage/DocsPage';
import HealingAgentPage from './pages/HealingAgentPage/HealingAgentPage';
import { useNavigation } from './hooks/useNavigation';

/**
 * Main App Component
 */
export default function App() {
  const { currentPage, navigateTo, goBack } = useNavigation('home');

  const renderPage = () => {
    switch (currentPage) {
      case 'testing':
        return <TestingPage onBack={goBack} />;
      case 'features':
        return <FeaturesPage />;
      case 'docs':
        return <DocsPage />;
      case 'healing':
        return <HealingAgentPage />;
      default:
        return <HomePage setPage={navigateTo} />;
    }
  };

  return (
    <div className="flex flex-col min-h-screen bg-[#0a0e1a] font-sans text-slate-100 overflow-x-hidden relative">
      {/* Global background orbs */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none z-0">
        <div className="absolute -top-40 -right-40 w-[500px] h-[500px] bg-indigo-500/[0.07] rounded-full blur-[120px] animate-orbFloat1" />
        <div className="absolute -bottom-40 -left-40 w-[500px] h-[500px] bg-purple-500/[0.07] rounded-full blur-[120px] animate-orbFloat2" />
        <div className="absolute top-1/3 left-1/2 -translate-x-1/2 w-[400px] h-[400px] bg-blue-500/[0.04] rounded-full blur-[100px]" />
      </div>

      <Navbar setPage={navigateTo} currentPage={currentPage} />
      <main className="flex-1 flex flex-col relative z-10">
        {renderPage()}
      </main>
    </div>
  );
}
