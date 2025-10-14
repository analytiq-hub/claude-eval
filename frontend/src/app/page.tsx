import React from 'react';

const Home = () => {
  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        {/* Hero Section */}
        <header className="text-center mb-12 hidden md:block">
          <h1 className="text-4xl font-bold text-gray-900 mb-4">
            <span>SigAgent</span>
          </h1>
          <div className="text-xl text-gray-600 space-y-1">
            <p>AI Agent Monitor</p>
            <p>Claude Code supported, other agents coming soon</p>
          </div>
        </header>
        
        <main>
          {/* About Section */}
          <section className="bg-white rounded-lg shadow-lg p-8 mb-12">
            <h2 className="text-2xl font-semibold text-gray-900 mb-4">About</h2>
            <p className="text-gray-600 mb-6">
              SigAgent is an AI agent monitoring platform that provides comprehensive telemetry and observability 
              for AI agents. Currently supporting Claude Code with plans to expand to other AI agents.
            </p>
            
            <div className="bg-gray-50 rounded-lg p-6">
              <h3 className="text-lg font-medium text-gray-900 mb-3">Key Features</h3>
              <ul className="list-disc list-inside text-gray-600 space-y-2">
                <li>Real-time telemetry collection via OTLP (OpenTelemetry Protocol)</li>
                <li>Comprehensive monitoring of traces, metrics, and logs</li>
                <li>Claude Code integration with automatic agent detection</li>
                <li>Organization-scoped data isolation and security</li>
                <li>REST APIs for telemetry data access and management</li>
                <li>Token-based authentication for secure agent communication</li>
              </ul>
            </div>
          </section>

          {/* Contact Section */}
          <section className="bg-white rounded-lg shadow-lg p-8">
            <h2 className="text-2xl font-semibold text-gray-900 mb-4">Contact Us</h2>
            <p className="text-gray-600 mb-4">
              Get in touch for inquiries, partnerships, or more information about our products.
            </p>
            <ul className="text-gray-600 space-y-2">
              <li>Email: <a href="mailto:andrei@analytiqhub.com" className="text-blue-600 hover:text-blue-800">andrei@analytiqhub.com</a></li>
              <li>Website: <a href="https://analytiqhub.com" className="text-blue-600 hover:text-blue-800">analytiqhub.com</a></li>
            </ul>
          </section>
        </main>
        
        <footer className="mt-12 text-center text-gray-600">
          <p>&copy; 2024 DocRouter.AI. All rights reserved.</p>
          <div className="mt-4">
            <a 
              href="https://github.com/analytiq-hub/doc-router" 
              className="text-blue-600 hover:text-blue-800"
            >
              View on GitHub
            </a>
          </div>
        </footer>
      </div>
    </div>
  );
};

export default Home;
